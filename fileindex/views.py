import os
import re
import string
import tempfile
import threading
from collections import Counter, defaultdict
from datetime import datetime

from django.conf import settings
from django.contrib.admin.views.decorators import staff_member_required
from django.db import connection, transaction
from django.http import HttpResponse, JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST
from openpyxl.utils import get_column_letter

from .drives import get_scan_locations
from .excel_parser import extract_numbers, parse_file
from .models import FileIndex, NumberIndex, ScanTask
from .report import build_report
from .scanner import EXCEL_EXTENSIONS, MIN_DIGITS, cancel_event, read_row, run_scan, scan_lock

MAX_NUMBERS = 100_000       # ek baar me screen par max numbers (file-wise summary chhoti rehti hai)
DETAIL_LIMIT = 2000         # isse zyada numbers par per-number detail nahi bhejte (page tez rahe)
FILES_LIMIT = 1000          # By File view me max files
FILE_LIST_CAP = 200         # har file ke liye max itne numbers ki list bhejte hain (count hamesha poora)
FILE_DETAIL_PAGE = 100      # 'View details' ek baar me itne numbers ki jagah laata hai
MAX_LOCATIONS_PER_NUMBER = 20
CHUNK = 500                 # DB query ek baar me itne numbers ki (SQLite limit se safe)
MAX_HITS_PER_NUMBER = 25    # screen par har number ke max matches
MAX_HITS_LARGE = 5          # badi list (LARGE_BATCH se zyada numbers) me screen response chhota rakhne ke liye
LARGE_BATCH = 200
EXPORT_MAX_HITS = 500       # Excel report me har number ke max matches
MAX_FILE_NUMBERS = 500_000  # upload file se full report me max numbers
EXPORT_MAX_HITS_HUGE = 50   # MAX_NUMBERS se badi file ki report me har number ke max matches (report chhoti rahe)
MAX_DETAIL_CELLS = 100
MAX_UPLOAD_MB = 25
XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


# ------------------------------------------------------------------ scan
def custom_path_allowed(user):
    """Specific folder scan sirf superuser ke liye. Band karna ho: settings me SCAN_ALLOW_CUSTOM_PATHS = False"""
    return bool(getattr(settings, "SCAN_ALLOW_CUSTOM_PATHS", True) and user.is_superuser)


@staff_member_required
@require_POST
def start_scan_api(request):
    key = request.POST.get("location", "")
    if key == "__custom__":
        if not custom_path_allowed(request.user):
            return JsonResponse({"error": "Specific folder scan is not allowed for your account"}, status=403)
        raw = request.POST.get("custom_path", "").strip().strip('"')
        root = os.path.normpath(raw) if raw else ""
        if not root or not os.path.isdir(root):
            return JsonResponse({"error": "Folder not found. Please check the path."}, status=400)
    else:
        # Client sirf key bhejta hai, asli path server detect karta hai (security)
        location = get_scan_locations().get(key)
        if not location:
            return JsonResponse({"error": "Invalid location"}, status=400)
        root = str(location["path"])

    if not scan_lock.acquire(blocking=False):
        return JsonResponse({"error": "Another scan or clear operation is already in progress. Please wait."}, status=409)

    try:
        # Lock free hai, matlab koi purana 'Running' task server restart se atak gaya tha
        ScanTask.objects.filter(status="Running").update(status="Error", message="Interrupted")
        task = ScanTask.objects.create(status="Running")
        threading.Thread(target=run_scan, args=(task.id, root), daemon=True).start()
    except Exception:
        scan_lock.release()
        raise
    return JsonResponse({"task_id": task.id, "status": "started"})


@staff_member_required
@require_POST
def stop_scan_api(request):
    """Chalte hue scan ko rok deta hai. Jo files index ho chuki hain wo rehti hain."""
    if not scan_lock.locked():
        return JsonResponse({"error": "No scan is running"}, status=409)
    cancel_event.set()
    return JsonResponse({"status": "stopping"})


@staff_member_required
@require_GET
def check_scan_status(request, task_id):
    task = ScanTask.objects.filter(id=task_id).first()
    if not task:
        return JsonResponse({"error": "Task not found"}, status=404)
    return JsonResponse({
        "status": task.status,
        "folders_scanned": task.folders_scanned,
        "files_indexed": task.files_indexed,
        "files_skipped": task.files_skipped,
        "files_failed": task.files_failed,
        "message": task.message,
        "elapsed": int((timezone.now() - task.created_at).total_seconds()),
    })


@staff_member_required
@require_GET
def browse_folders_api(request):
    """Folder picker ke liye: diye gaye path ke andar ke sub-folders ki list."""
    if not custom_path_allowed(request.user):
        return JsonResponse({"error": "Not allowed"}, status=403)

    path = request.GET.get("path", "").strip()
    if not path:
        if os.name == "nt":   # Windows: drives ki list
            drives = [f"{c}:\\" for c in string.ascii_uppercase if os.path.exists(f"{c}:\\")]
            return JsonResponse({"path": "", "parent": None,
                                 "folders": [{"name": d, "path": d} for d in drives]})
        path = os.sep

    path = os.path.normpath(path)
    if not os.path.isdir(path):
        return JsonResponse({"error": "Folder not found"}, status=400)

    folders = []
    try:
        with os.scandir(path) as it:
            for entry in it:
                try:
                    if entry.is_dir(follow_symlinks=False) and not entry.name.startswith(("$", ".")):
                        folders.append({"name": entry.name, "path": entry.path})
                except OSError:
                    continue
    except PermissionError:
        return JsonResponse({"error": "Permission denied for this folder"}, status=403)

    folders.sort(key=lambda f: f["name"].lower())
    parent = os.path.dirname(path)
    if parent == path:   # root par hain: Windows me drive list, Linux me upar kuch nahi
        parent = "" if os.name == "nt" else None
    return JsonResponse({"path": path, "parent": parent, "folders": folders[:1000]})


# ------------------------------------------------------------------ search
def _parse_numbers(raw):
    """(valid numbers, skipped entries, duplicate count)"""
    numbers, seen, skipped, duplicates = [], set(), [], 0
    for part in re.split(r"[,;\n]+", raw):
        part = part.strip()
        if not part:
            continue
        n = re.sub(r"\D", "", part)
        if len(n) < MIN_DIGITS:
            skipped.append(part[:40])
        elif n in seen:
            duplicates += 1
        else:
            seen.add(n)
            numbers.append(n)
    return numbers, skipped, duplicates


def _fmt_mtime(mtime):
    return datetime.fromtimestamp(mtime).strftime("%d %b %Y, %H:%M") if mtime else ""


def _lookup_chunks(numbers, max_hits, file_stats=None):
    """500-500 numbers ka indexed lookup. Har chunk ke liye (numbers, matches, totals, files) yield karta hai.

    max_hits=0 : per-number matches nahi banate (badi list me tez).
    file_stats : dict diya ho toh file-wise summary bharta hai:
                 {file_id: {file, folder, modified, numbers_count, matches, numbers[:FILE_LIST_CAP]}}
    """
    for i in range(0, len(numbers), CHUNK):
        chunk = numbers[i:i + CHUNK]
        rows = NumberIndex.objects.filter(number__in=chunk).values_list(
            "number", "file_id", "file__file_name", "file__file_path", "file__file_mtime", "sheet", "row", "col"
        )
        matches, totals, files = defaultdict(list), Counter(), defaultdict(set)
        seen_pairs = set()   # (file, number): ek number ek file me kitni bhi baar ho, count 1 baar
        for number, file_id, name, path, mtime, sheet, row, col in rows.iterator():
            totals[number] += 1
            if max_hits:
                files[number].add(file_id)
                if len(matches[number]) < max_hits:
                    matches[number].append({
                        "file_id": file_id,
                        "file": name,
                        "folder": os.path.dirname(path),
                        "sheet": sheet,
                        "row": row,
                        "col": col,
                        "column": get_column_letter(col) if col else "",
                        "modified": _fmt_mtime(mtime),
                    })
            if file_stats is not None:
                fs = file_stats.get(file_id)
                if fs is None:
                    fs = file_stats[file_id] = {
                        "file_id": file_id, "file": name, "folder": os.path.dirname(path),
                        "modified": _fmt_mtime(mtime), "numbers_count": 0, "matches": 0, "numbers": [],
                    }
                fs["matches"] += 1
                pair = (file_id, number)
                if pair not in seen_pairs:
                    seen_pairs.add(pair)
                    fs["numbers_count"] += 1
                    if len(fs["numbers"]) < FILE_LIST_CAP:
                        fs["numbers"].append(number)
        for lst in matches.values():   # DB me ORDER BY nahi, chhoti list Python me sort (tez)
            lst.sort(key=lambda m: (m["file"].lower(), m["sheet"], m["row"], m["col"]))
        yield chunk, matches, totals, files


@staff_member_required
@require_POST
def bulk_search_api(request):
    numbers, skipped, duplicates = _parse_numbers(request.POST.get("numbers", ""))
    truncated = len(numbers) > MAX_NUMBERS
    numbers = numbers[:MAX_NUMBERS]
    detail = len(numbers) <= DETAIL_LIMIT   # per-number details sirf chhoti list ke liye
    max_hits = (MAX_HITS_PER_NUMBER if len(numbers) <= LARGE_BATCH else MAX_HITS_LARGE) if detail else 0

    file_stats, results, not_found, found = {}, [], [], 0
    for chunk, matches, totals, files in _lookup_chunks(numbers, max_hits, file_stats):
        for n in chunk:
            if totals[n]:
                found += 1
            else:
                not_found.append(n)
            if detail:
                results.append({
                    "number": n,
                    "found": totals[n] > 0,
                    "total": totals[n],           # kul kitni jagah mila
                    "file_count": len(files[n]),  # kitni alag files me
                    "matches": matches[n],
                })

    by_file = sorted(file_stats.values(), key=lambda f: (-f["numbers_count"], f["file"].lower()))
    return JsonResponse({
        "status": "success",
        "summary": {
            "searched": len(numbers),
            "found": found,
            "not_found": len(not_found),
            "files": len(by_file),
            "duplicates": duplicates,
            "skipped": skipped,
            "min_digits": MIN_DIGITS,
            "truncated": truncated,
            "max_numbers": MAX_NUMBERS,
            "detail": detail,
            "detail_limit": DETAIL_LIMIT,
        },
        "files": by_file[:FILES_LIMIT],
        "results": results,
        "not_found": not_found,
    })


@staff_member_required
@require_POST
def file_locations_api(request):
    """'View details' click par: ek file me diye gaye numbers ki jagah (sheet / row / column)."""
    try:
        file_id = int(request.POST.get("file_id", ""))
    except ValueError:
        return JsonResponse({"error": "Invalid request"}, status=400)
    numbers, _, _ = _parse_numbers(request.POST.get("numbers", ""))
    numbers = numbers[:FILE_DETAIL_PAGE]

    rows = NumberIndex.objects.filter(file_id=file_id, number__in=numbers).values_list(
        "number", "sheet", "row", "col")
    by_number = defaultdict(list)
    for number, sheet, row, col in rows:
        if len(by_number[number]) < MAX_LOCATIONS_PER_NUMBER:
            by_number[number].append({"sheet": sheet, "row": row, "col": col,
                                      "column": get_column_letter(col) if col else ""})
    items = []
    for n in numbers:   # numbers ka order wahi jo list me tha
        if n in by_number:
            by_number[n].sort(key=lambda loc: (loc["sheet"], loc["row"], loc["col"]))
            items.append({"number": n, "locations": by_number[n]})
    return JsonResponse({"file_id": file_id, "items": items})


@staff_member_required
@require_GET
def row_detail_api(request):
    """'View full row' click par us row ka poora data file se padhkar deta hai."""
    try:
        file_id = int(request.GET.get("file_id", ""))
        row_no = int(request.GET.get("row", ""))
        col = int(request.GET.get("col", "0") or 0)
    except ValueError:
        return JsonResponse({"error": "Invalid request"}, status=400)

    f = FileIndex.objects.filter(id=file_id).first()   # path DB se aata hai, client se nahi
    if not f:
        return JsonResponse({"error": "File not in index"}, status=404)

    try:
        headers, values = read_row(f.file_path, request.GET.get("sheet", ""), row_no)
    except FileNotFoundError:
        return JsonResponse({"error": "File moved or deleted. Run the scan again."}, status=404)
    except Exception as exc:
        return JsonResponse({"error": f"Could not read row: {exc}"}, status=500)

    cells = []
    for i in range(min(max(len(headers), len(values)), MAX_DETAIL_CELLS)):
        h = headers[i] if i < len(headers) else ""
        v = values[i] if i < len(values) else ""
        if h or v:
            cells.append({"col": get_column_letter(i + 1), "header": h, "value": v, "hit": (i + 1) == col})
    return JsonResponse({"cells": cells})


# ------------------------------------------------------------------ upload numbers from file
def _numbers_from_text(upload, first_col):
    raw = upload.read()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")
    found = []
    for line in text.splitlines():
        parts = re.split(r"[,;\t]", line)
        for part in (parts[:1] if first_col else parts):
            found.extend(extract_numbers(part.strip().strip('"\''), MIN_DIGITS))   # CSV ke quotes hatao
    return found


def _numbers_from_excel(upload, ext, first_col):
    fd, tmp = tempfile.mkstemp(suffix=ext)
    try:
        with os.fdopen(fd, "wb") as f:
            for chunk in upload.chunks():
                f.write(chunk)
        hits = parse_file(tmp, MIN_DIGITS)   # scanner wala hi fast reader (calamine / openpyxl)
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass
    if first_col:
        hits = [h for h in hits if h[3] == 1]
    hits.sort(key=lambda h: (h[1], h[2], h[3]))   # sheet, row, column ke order me
    return [h[0] for h in hits]


def _read_upload(request):
    """Upload se numbers nikalta hai. Returns ((file name, unique numbers, duplicates), None) ya (None, error response)."""
    upload = request.FILES.get("file")
    if not upload:
        return None, JsonResponse({"error": "No file received"}, status=400)
    if upload.size > MAX_UPLOAD_MB * 1024 * 1024:
        return None, JsonResponse({"error": f"File is too large (max {MAX_UPLOAD_MB} MB)"}, status=413)

    ext = os.path.splitext(upload.name)[1].lower()
    first_col = bool(request.POST.get("first_col"))
    try:
        if ext in (".csv", ".txt"):
            found = _numbers_from_text(upload, first_col)
        elif ext in EXCEL_EXTENSIONS:
            found = _numbers_from_excel(upload, ext, first_col)
        else:
            return None, JsonResponse({"error": "Unsupported file. Use .xlsx, .xls, .csv or .txt"}, status=400)
    except Exception as exc:
        return None, JsonResponse({"error": f"Could not read file: {exc}"}, status=400)

    unique = list(dict.fromkeys(found))   # order maintain, duplicates hatao
    return (upload.name, unique, len(found) - len(unique)), None


@staff_member_required
@require_POST
def extract_numbers_api(request):
    """Uploaded file se numbers nikal kar deta hai (screen par dikhane ke liye, max MAX_NUMBERS)."""
    result, error = _read_upload(request)
    if error:
        return error
    name, unique, _ = result
    return JsonResponse({
        "file": name,
        "numbers": unique[:MAX_NUMBERS],
        "total": len(unique),
        "truncated": len(unique) > MAX_NUMBERS,
        "max": MAX_NUMBERS,
    })


def _xlsx_response(data):
    filename = datetime.now().strftime("number_search_report_%Y%m%d_%H%M.xlsx")
    response = HttpResponse(data, content_type=XLSX_TYPE)
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


@staff_member_required
@require_POST
def file_report_api(request):
    """Badi file ke SAARE numbers search karke seedha Excel report deta hai (screen limit ke bina)."""
    result, error = _read_upload(request)
    if error:
        return error
    _name, numbers, duplicates = result
    if not numbers:
        return JsonResponse({"error": "No numbers found in the file"}, status=400)

    numbers = numbers[:MAX_FILE_NUMBERS]
    max_hits = EXPORT_MAX_HITS if len(numbers) <= DETAIL_LIMIT else EXPORT_MAX_HITS_HUGE
    file_stats = {}
    data = build_report(_lookup_chunks(numbers, max_hits, file_stats), len(numbers), [], duplicates,
                        max_hits, file_stats)
    return _xlsx_response(data)


# ------------------------------------------------------------------ Excel report
@staff_member_required
@require_POST
def export_excel_api(request):
    numbers, skipped, duplicates = _parse_numbers(request.POST.get("numbers", ""))
    numbers = numbers[:MAX_NUMBERS]
    max_hits = EXPORT_MAX_HITS if len(numbers) <= DETAIL_LIMIT else EXPORT_MAX_HITS_HUGE
    file_stats = {}
    data = build_report(_lookup_chunks(numbers, max_hits, file_stats), len(numbers), skipped, duplicates,
                        max_hits, file_stats)
    return _xlsx_response(data)


@staff_member_required
@require_POST
def clear_index_api(request):
    """Poora index (files + numbers) ek jhatke me hatata hai. Disk ki Excel files ko haath nahi lagata."""
    if not request.user.has_perm("fileindex.delete_fileindex"):
        return JsonResponse({"error": "You do not have permission to clear the index"}, status=403)
    if not scan_lock.acquire(blocking=False):   # lock pakda rehta hai, isliye clear ke dauraan naya scan shuru nahi hoga
        return JsonResponse({"error": "A scan is running. Please wait for it to finish."}, status=409)
    try:
        files_removed = FileIndex.objects.count()
        qn = connection.ops.quote_name
        with transaction.atomic(), connection.cursor() as cur:
            # Raw DELETE: Django ke .delete() se bahut tez (10 lakh rows < 1 second)
            cur.execute(f"DELETE FROM {qn(NumberIndex._meta.db_table)}")
            cur.execute(f"DELETE FROM {qn(FileIndex._meta.db_table)}")
        if connection.vendor == "sqlite":
            try:
                connection.cursor().execute("VACUUM")   # database file ka size bhi chhota ho jata hai
            except Exception:
                pass
    finally:
        scan_lock.release()
    return JsonResponse({"status": "cleared", "files_removed": files_removed})