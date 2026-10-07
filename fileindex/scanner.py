import logging
import multiprocessing
import os
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from concurrent.futures.process import BrokenProcessPool

from django.conf import settings
from django.db import connection, transaction

from .excel_parser import HAS_CALAMINE, parse_file, read_row  # noqa: F401  (read_row views.py use karta hai)
from .models import FileIndex, NumberIndex, ScanTask

logger = logging.getLogger(__name__)

MIN_DIGITS = getattr(settings, "SCAN_MIN_DIGITS", 5)
SKIP_DIRS = {d.lower() for d in getattr(settings, "SCAN_SKIP_DIRS", ())}
# Excel padhne wale parallel processes. 1 = parallel band (debug ke liye)
WORKERS = getattr(settings, "SCAN_WORKERS", min(4, max(1, (os.cpu_count() or 2) - 1)))
MAX_IN_FLIGHT = WORKERS * 2   # ek time me itni files memory me (RAM control)

EXCEL_EXTENSIONS = (".xlsx", ".xlsm", ".xls") + ((".xlsb",) if HAS_CALAMINE else ())
SAVE_EVERY_SECONDS = 1.5
INDEX_VERSION = 2  # badhao toh sab files agle scan me dobara index hongi
PROGRESS_FIELDS = ["folders_scanned", "files_indexed", "files_skipped", "files_failed", "message"]

# Ek time me sirf ek scan chale
scan_lock = threading.Lock()
# Stop Scan button isse scan ko rukne ka signal deta hai
cancel_event = threading.Event()


def _make_pool():
    if WORKERS <= 1:
        return None
    # 'spawn': naye clean processes (Django DB connection copy nahi hota), Windows jaisa hi behaviour
    return ProcessPoolExecutor(max_workers=WORKERS, mp_context=multiprocessing.get_context("spawn"))


def _store(path, name, stat, hits):
    """File ke numbers DB me daalta hai. Raw executemany, Django model objects se kaafi fast."""
    qn = connection.ops.quote_name
    sql = "INSERT INTO {} ({}) VALUES (%s, %s, %s, %s, %s)".format(
        qn(NumberIndex._meta.db_table),
        ", ".join(qn(c) for c in ("file_id", "number", "sheet", "row", "col")),
    )
    with transaction.atomic():
        obj, _ = FileIndex.objects.update_or_create(
            file_path=path,
            defaults={"file_name": name, "file_size": stat.st_size, "file_mtime": stat.st_mtime,
                      "index_version": INDEX_VERSION},
        )
        NumberIndex.objects.filter(file=obj).delete()
        if hits:
            with connection.cursor() as cur:
                cur.executemany(sql, [(obj.id, n, s, r, c) for n, s, r, c in hits])


def run_scan(task_id, root):
    """Background thread. Sirf badli/nayi files padhta hai, aur wo bhi parallel processes me."""
    pool = None
    cancelled = False
    cancel_event.clear()
    try:
        task = ScanTask.objects.get(id=task_id)
        if not os.path.isdir(root):
            raise FileNotFoundError(f"Folder not found: {root}")
        root = os.path.normpath(root)
        # Trailing separator zaruri: 'D:\\Data' scan karte waqt 'D:\\Data2' ki files na chhui jayein
        prefix = root if root.endswith(os.sep) else root + os.sep
        if connection.vendor == "sqlite":
            connection.cursor().execute("PRAGMA synchronous=NORMAL")  # WAL ke saath safe aur fast

        existing = {
            p: (s, m, v)
            for p, s, m, v in FileIndex.objects.filter(file_path__startswith=prefix)
            .values_list("file_path", "file_size", "file_mtime", "index_version")
        }
        seen = set()
        pending = {}   # future -> (path, name, stat)
        last_save = time.monotonic()
        pool = _make_pool()

        def save_result(path, name, stat, hits):
            try:
                _store(path, name, stat, hits)
                task.files_indexed += 1
            except Exception as exc:
                task.files_failed += 1
                logger.warning("DB write failed for %s: %s", path, exc)

        def drain(limit):
            """Pending futures ko 'limit' tak kam karo; jo file padhi ja chuki hai use DB me likho."""
            nonlocal pool
            while len(pending) > limit:
                done, _ = wait(pending, return_when=FIRST_COMPLETED)
                broken = False
                for fut in done:
                    path, name, stat = pending.pop(fut)
                    try:
                        hits = fut.result()
                    except BrokenProcessPool:
                        broken = True
                        task.files_failed += 1
                        logger.warning("Worker crashed on %s (RAM kam? SCAN_WORKERS kam karo)", path)
                        continue
                    except Exception as exc:
                        task.files_failed += 1
                        logger.warning("Failed to read %s: %s", path, exc)
                        continue
                    save_result(path, name, stat, hits)
                if broken:  # crashed pool badal do
                    pool.shutdown(wait=False)
                    pool = _make_pool()

        for dirpath, dirnames, filenames in os.walk(root):
            if cancel_event.is_set():
                cancelled = True
                break
            dirnames[:] = [d for d in dirnames if d.lower() not in SKIP_DIRS]  # system folders skip
            task.folders_scanned += 1

            for fname in filenames:
                if cancel_event.is_set():
                    cancelled = True
                    break
                if fname.startswith("~$") or not fname.lower().endswith(EXCEL_EXTENSIONS):
                    continue
                path = os.path.join(dirpath, fname)
                seen.add(path)
                try:
                    stat = os.stat(path)
                except OSError as exc:
                    task.files_failed += 1
                    logger.warning("Cannot stat %s: %s", path, exc)
                    continue

                if existing.get(path) == (stat.st_size, stat.st_mtime, INDEX_VERSION):
                    task.files_skipped += 1   # file badli nahi
                else:
                    task.message = fname
                    if pool is None:
                        try:
                            save_result(path, fname, stat, parse_file(path, MIN_DIGITS))
                        except Exception as exc:
                            task.files_failed += 1
                            logger.warning("Failed to read %s: %s", path, exc)
                    else:
                        try:
                            fut = pool.submit(parse_file, path, MIN_DIGITS)
                        except BrokenProcessPool:
                            pool = _make_pool()
                            fut = pool.submit(parse_file, path, MIN_DIGITS)
                        pending[fut] = (path, fname, stat)
                        drain(MAX_IN_FLIGHT - 1)

                if time.monotonic() - last_save > SAVE_EVERY_SECONDS:
                    task.save(update_fields=PROGRESS_FIELDS)
                    last_save = time.monotonic()

        if cancelled:
            # Adhoori padhi files chhod do. Jo index ban chuka hai wo rahega. Deleted-files ki safai nahi karte,
            # kyunki scan poora nahi hua (warna na-scan hui files galti se index se hat jaati).
            pending.clear()
            task.status = "Cancelled"
            task.message = "Stopped by user"
            task.save(update_fields=PROGRESS_FIELDS + ["status"])
        else:
            drain(0)  # bachi hui files poori karo

            # Disk se delete ho chuki files ko index se hatao
            stale = [p for p in existing if p not in seen]
            for i in range(0, len(stale), 500):
                FileIndex.objects.filter(file_path__in=stale[i:i + 500]).delete()

            task.status = "Completed"
            task.message = ""
            task.save(update_fields=PROGRESS_FIELDS + ["status"])
    except Exception as exc:
        logger.exception("Scan failed")
        ScanTask.objects.filter(id=task_id).update(status="Error", message=str(exc)[:500])
    finally:
        # Yahan koi bhi error aaye, lock HAMESHA release hona chahiye. Warna page hamesha "scan chal raha hai"
        # maanta rahega aur dropdown / Start button locked rehte hain.
        try:
            if pool is not None:
                pool.shutdown(wait=not cancelled, cancel_futures=True)   # Stop par chalte kaam ka intezaar nahi
        except Exception:
            logger.exception("Could not shut down the worker pool")
        try:
            connection.close()
        except Exception:
            pass
        scan_lock.release()