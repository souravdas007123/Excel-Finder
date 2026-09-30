import logging
import os
import re
import threading
import time
from datetime import date, datetime

from django.conf import settings
from django.db import connection, transaction

from .models import FileIndex, NumberIndex, ScanTask

logger = logging.getLogger(__name__)

MIN_DIGITS = getattr(settings, "SCAN_MIN_DIGITS", 5)
MAX_DIGITS = 32
SKIP_DIRS = {d.lower() for d in getattr(settings, "SCAN_SKIP_DIRS", ())}
EXCEL_EXTENSIONS = (".xlsx", ".xlsm", ".xls")
SAVE_EVERY_SECONDS = 1.5
INDEX_VERSION = 2  # badhao toh sab files agle scan me dobara index hongi
PROGRESS_FIELDS = ["folders_scanned", "files_indexed", "files_skipped", "files_failed", "message"]

# Ek time me sirf ek scan chale
scan_lock = threading.Lock()

_SEPARATORS = re.compile(r"[\s\-+()]")
_DIGITS = re.compile(r"\d+")


def extract_numbers(value):
    """Cell se numbers nikalta hai. '98563 25417', '9856325417.0' sab '9856325417' ban jaate hain."""
    text = str(value).strip()
    if not text:
        return []
    if text.endswith(".0"):
        text = text[:-2]
    compact = _SEPARATORS.sub("", text)
    found = [compact] if (compact.isascii() and compact.isdigit()) else _DIGITS.findall(text)
    return [n for n in found if MIN_DIGITS <= len(n) <= MAX_DIGITS]


def iter_rows(path):
    """Yield (sheet_name, row_number, values). pandas se bahut fast + saari sheets."""
    if path.lower().endswith(".xls"):
        import xlrd
        book = xlrd.open_workbook(path, on_demand=True)
        try:
            for sheet in book.sheets():
                for r in range(sheet.nrows):
                    yield sheet.name, r + 1, sheet.row_values(r)
        finally:
            book.release_resources()
    else:
        from openpyxl import load_workbook
        wb = load_workbook(path, read_only=True, data_only=True)
        try:
            for ws in wb.worksheets:
                for r, row in enumerate(ws.iter_rows(values_only=True), 1):
                    yield ws.title, r, row
        finally:
            wb.close()


def index_file(path, name, stat):
    hits = set()  # (number, sheet, row, col) - duplicates automatically hat jaate hain
    for sheet, row_no, values in iter_rows(path):
        for col_no, v in enumerate(values, 1):
            if v is None or v == "" or isinstance(v, (datetime, date)):
                continue
            for n in extract_numbers(v):
                hits.add((n, sheet[:100], row_no, col_no))

    with transaction.atomic():
        obj, _ = FileIndex.objects.update_or_create(
            file_path=path,
            defaults={"file_name": name, "file_size": stat.st_size, "file_mtime": stat.st_mtime,
                      "index_version": INDEX_VERSION},
        )
        obj.numbers.all().delete()
        NumberIndex.objects.bulk_create(
            [NumberIndex(file=obj, number=n, sheet=s, row=r, col=c) for n, s, r, c in hits]
        )


def run_scan(task_id, root):
    """Background thread. Sirf badli/nayi files dobara padhta hai."""
    try:
        task = ScanTask.objects.get(id=task_id)
        if not os.path.isdir(root):
            raise FileNotFoundError(f"Folder not found: {root}")

        existing = {
            p: (s, m, v)
            for p, s, m, v in FileIndex.objects.filter(file_path__startswith=root)
            .values_list("file_path", "file_size", "file_mtime", "index_version")
        }
        seen = set()
        last_save = time.monotonic()

        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d.lower() not in SKIP_DIRS]  # system folders skip
            task.folders_scanned += 1

            for fname in filenames:
                if fname.startswith("~$") or not fname.lower().endswith(EXCEL_EXTENSIONS):
                    continue
                path = os.path.join(dirpath, fname)
                seen.add(path)
                try:
                    stat = os.stat(path)
                    if existing.get(path) == (stat.st_size, stat.st_mtime, INDEX_VERSION):
                        task.files_skipped += 1
                    else:
                        task.message = fname
                        index_file(path, fname, stat)
                        task.files_indexed += 1
                except Exception as exc:
                    task.files_failed += 1
                    logger.warning("Failed to index %s: %s", path, exc)

                if time.monotonic() - last_save > SAVE_EVERY_SECONDS:
                    task.save(update_fields=PROGRESS_FIELDS)
                    last_save = time.monotonic()

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
        connection.close()
        scan_lock.release()


def _cell_text(v):
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v).strip()


def read_row(path, sheet_name, row_no):
    """Ek row + sheet ki pehli row (header) padhta hai. Click par hi chalta hai, isliye DB me store nahi hota."""
    if path.lower().endswith(".xls"):
        import xlrd
        book = xlrd.open_workbook(path, on_demand=True)
        try:
            sh = book.sheet_by_name(sheet_name)
            headers = sh.row_values(0) if sh.nrows else []
            values = sh.row_values(row_no - 1) if 0 < row_no <= sh.nrows else []
        finally:
            book.release_resources()
    else:
        from openpyxl import load_workbook
        wb = load_workbook(path, read_only=True, data_only=True)
        try:
            ws = wb[sheet_name]
            headers = next(ws.iter_rows(min_row=1, max_row=1, values_only=True), ())
            values = next(ws.iter_rows(min_row=row_no, max_row=row_no, values_only=True), ())
        finally:
            wb.close()
    return [_cell_text(h) for h in headers], [_cell_text(v) for v in values]
