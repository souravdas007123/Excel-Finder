import os
import re
import threading
from collections import Counter, defaultdict
from datetime import datetime

from django.conf import settings
from django.contrib.admin.views.decorators import staff_member_required
from django.http import JsonResponse
from django.views.decorators.http import require_GET, require_POST
from openpyxl.utils import get_column_letter

from .models import FileIndex, NumberIndex, ScanTask
from .scanner import MIN_DIGITS, read_row, run_scan, scan_lock

MAX_NUMBERS = 500          # ek baar me max numbers
MAX_HITS_PER_NUMBER = 25   # response chhota rakhne ke liye
MAX_DETAIL_CELLS = 100


@staff_member_required
@require_POST
def start_scan_api(request):
    # Client sirf key bhejta hai, asli path server ke settings se aata hai (security)
    location = settings.SCAN_LOCATIONS.get(request.POST.get("location", ""))
    if not location:
        return JsonResponse({"error": "Invalid location"}, status=400)

    if not scan_lock.acquire(blocking=False):
        return JsonResponse({"error": "A scan is already running"}, status=409)

    try:
        # Lock free hai, matlab koi purana 'Running' task server restart se atak gaya tha
        ScanTask.objects.filter(status="Running").update(status="Error", message="Interrupted")
        task = ScanTask.objects.create(status="Running")
        threading.Thread(target=run_scan, args=(task.id, str(location["path"])), daemon=True).start()
    except Exception:
        scan_lock.release()
        raise
    return JsonResponse({"task_id": task.id, "status": "started"})


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
    })


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


@staff_member_required
@require_POST
def bulk_search_api(request):
    numbers, skipped, duplicates = _parse_numbers(request.POST.get("numbers", ""))
    truncated = len(numbers) > MAX_NUMBERS
    numbers = numbers[:MAX_NUMBERS]

    # Ek hi indexed query saare numbers ke liye
    rows = (
        NumberIndex.objects.filter(number__in=numbers)
        .order_by("file__file_name", "sheet", "row", "col")
        .values_list("number", "file_id", "file__file_name", "file__file_path",
                     "file__file_mtime", "sheet", "row", "col")
    )
    matches, totals, files = defaultdict(list), Counter(), defaultdict(set)
    for number, file_id, name, path, mtime, sheet, row, col in rows.iterator():
        totals[number] += 1
        files[number].add(file_id)
        if len(matches[number]) < MAX_HITS_PER_NUMBER:
            matches[number].append({
                "file_id": file_id,
                "file": name,
                "folder": os.path.dirname(path),
                "sheet": sheet,
                "row": row,
                "col": col,
                "column": get_column_letter(col) if col else "",
                "modified": datetime.fromtimestamp(mtime).strftime("%d %b %Y, %H:%M") if mtime else "",
            })

    results = [
        {
            "number": n,
            "found": totals[n] > 0,
            "total": totals[n],          # kul kitni jagah mila
            "file_count": len(files[n]), # kitni alag files me
            "matches": matches[n],
        }
        for n in numbers
    ]
    found = sum(1 for r in results if r["found"])
    return JsonResponse({
        "status": "success",
        "results": results,
        "summary": {
            "searched": len(numbers),
            "found": found,
            "not_found": len(numbers) - found,
            "duplicates": duplicates,
            "skipped": skipped,
            "min_digits": MIN_DIGITS,
            "truncated": truncated,
            "max_numbers": MAX_NUMBERS,
        },
    })


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
