import logging
import multiprocessing
import os
import threading
import time
import zipfile
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from concurrent.futures.process import BrokenProcessPool

from django.conf import settings
from django.db import connection, transaction

from .excel_parser import HAS_CALAMINE, match_key, parse_file, read_row  # noqa: F401  (read_row views.py use karta hai)
from .models import FileIndex, NumberIndex, ScanFailure, ScanTask

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
# Scan ke saath alag thread Excel files ginta hai, taaki "kitna baaki" (ETA) dikha sakein. Ek time me ek hi scan.
scan_progress = {"run": None, "task_id": None, "total": None, "counted": 0}
STORE_BATCH = 20_000   # badi file ek saath nahi, itni-itni rows me DB me likhte hain (beech me Stop check ho sake)


MAX_FAILURES_STORED = 500   # ek scan me itni failed files ki list save hoti hai (count hamesha poora)


def _friendly_reason(exc):
    """Failed file ki wajah aasaan bhasha me (Scan History page par dikhti hai)."""
    text = str(exc)
    low = text.lower()
    if isinstance(exc, PermissionError):
        return "No permission to read this file (is it open or locked?)"
    if isinstance(exc, FileNotFoundError):
        return "File was moved or deleted during the scan"
    if isinstance(exc, MemoryError):
        return "File is too big to read (out of memory)"
    if "password" in low or "encrypt" in low:
        return "Password-protected file"
    if isinstance(exc, zipfile.BadZipFile) or "zip" in low or "not a valid" in low or "corrupt" in low:
        return "File is corrupted or not a real Excel file"
    return f"{type(exc).__name__}: {text}"[:300]


class ScanCancelled(Exception):
    """Stop dabne par chalti hui file ka DB write rok kar rollback karne ke liye."""


def _kill_workers(pool):
    """Stop par worker processes turant band karo (warna badi file padhne wale worker minutes tak chalte rehte hain)."""
    try:
        for proc in list(getattr(pool, "_processes", {}).values()):
            proc.terminate()
    except Exception:
        logger.exception("Could not stop worker processes")


def _count_excel_files(root, run):
    """Background me kul Excel files gino (scan ke saath chalta hai). Scan se zyada tez chalta hai, kyunki file padhni nahi padti."""
    counted = 0
    try:
        for _dirpath, dirnames, filenames in os.walk(root):
            if cancel_event.is_set() or scan_progress["run"] is not run:
                return
            dirnames[:] = [d for d in dirnames if d.lower() not in SKIP_DIRS]
            counted += sum(1 for f in filenames if not f.startswith("~$") and f.lower().endswith(EXCEL_EXTENSIONS))
            scan_progress["counted"] = counted
        if scan_progress["run"] is run:
            scan_progress["total"] = counted
    except Exception:
        logger.exception("Could not count Excel files for the scan estimate")


def _make_pool():
    if WORKERS <= 1:
        return None
    # 'spawn': naye clean processes (Django DB connection copy nahi hota), Windows jaisa hi behaviour
    return ProcessPoolExecutor(max_workers=WORKERS, mp_context=multiprocessing.get_context("spawn"))


def _store(path, name, stat, hits):
    """File ke numbers DB me daalta hai. Raw executemany, Django model objects se kaafi fast."""
    qn = connection.ops.quote_name
    sql = "INSERT INTO {} ({}) VALUES (%s, %s, %s, %s, %s, %s)".format(
        qn(NumberIndex._meta.db_table),
        ", ".join(qn(c) for c in ("file_id", "number", "match_key", "sheet", "row", "col")),
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
                for i in range(0, len(hits), STORE_BATCH):
                    if cancel_event.is_set():
                        raise ScanCancelled()   # atomic block rollback ho jayega, adhoori file index me nahi rehti
                    cur.executemany(sql, [(obj.id, n, match_key(n), s, r, c) for n, s, r, c in hits[i:i + STORE_BATCH]])


def run_scan(task_id, root):
    """Background thread. Sirf badli/nayi files padhta hai, aur wo bhi parallel processes me."""
    pool = None
    cancelled = False
    failures = []   # (path, reason): kaun si file fail hui
    cancel_event.clear()
    try:
        task = ScanTask.objects.get(id=task_id)
        if not os.path.isdir(root):
            raise FileNotFoundError(f"Folder not found: {root}")
        root = os.path.normpath(root)
        run = object()   # is scan ki pehchaan: purane scan ka counting thread naye scan ka data na badle
        scan_progress.update(run=run, task_id=task_id, total=None, counted=0)
        threading.Thread(target=_count_excel_files, args=(root, run), daemon=True).start()
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

        def fail(path, reason):
            task.files_failed += 1
            if len(failures) < MAX_FAILURES_STORED:
                failures.append((path, reason))

        def save_result(path, name, stat, hits):
            try:
                _store(path, name, stat, hits)
                task.files_indexed += 1
            except ScanCancelled:
                pass   # Stop dabaya gaya; loops cancel_event dekh kar ruk jayenge
            except Exception as exc:
                fail(path, "Could not save to the database: " + str(exc)[:200])
                logger.warning("DB write failed for %s: %s", path, exc)

        def drain(limit):
            """Pending futures ko 'limit' tak kam karo; jo file padhi ja chuki hai use DB me likho."""
            nonlocal pool
            while len(pending) > limit:
                if cancel_event.is_set():
                    return
                done, _ = wait(pending, timeout=0.5, return_when=FIRST_COMPLETED)   # timeout: beech me Stop dekh sakein
                if not done:
                    continue
                broken = False
                for fut in done:
                    path, name, stat = pending.pop(fut)
                    try:
                        hits = fut.result()
                    except BrokenProcessPool:
                        broken = True
                        fail(path, "Reader crashed on this file (too big or damaged)")
                        logger.warning("Worker crashed on %s (RAM kam? SCAN_WORKERS kam karo)", path)
                        continue
                    except Exception as exc:
                        fail(path, _friendly_reason(exc))
                        logger.warning("Failed to read %s: %s", path, exc)
                        continue
                    save_result(path, name, stat, hits)
                    if cancel_event.is_set():
                        return
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
                    fail(path, _friendly_reason(exc))
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
                            fail(path, _friendly_reason(exc))
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

        if not cancelled:
            drain(0)   # bachi hui files poori karo
            cancelled = cancel_event.is_set()   # drain ke dauraan Stop dabaya ho toh bhi cancel maano

        if cancelled:
            if pool is not None:
                _kill_workers(pool)
            # Adhoori padhi files chhod do. Jo index ban chuka hai wo rahega. Deleted-files ki safai nahi karte,
            # kyunki scan poora nahi hua (warna na-scan hui files galti se index se hat jaati).
            pending.clear()
            task.status = "Cancelled"
            task.message = "Stopped by user"
            task.save(update_fields=PROGRESS_FIELDS + ["status"])
        else:
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
            if failures:   # failed files ki list save (scan khatam, cancel ya error: teeno me)
                ScanFailure.objects.bulk_create(
                    [ScanFailure(task_id=task_id, file_path=p[:1000], reason=r[:500]) for p, r in failures])
        except Exception:
            logger.exception("Could not save the failed-files list")
        try:
            connection.close()
        except Exception:
            pass
        scan_lock.release()