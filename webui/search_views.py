"""Bulk search page: numbers paste / upload, natija (By File / By Number / Not Found), row detail, file detail."""
import secrets

from django.core.cache import cache
from django.http import Http404, HttpResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET, require_POST

from fileindex import views as core
from fileindex.models import FileIndex
from fileindex.scanner import MIN_DIGITS

from .helpers import message_only, staff_required
from .views import _freshness

TTL = 2 * 3600          # search ka natija itni der yaad rehta hai (filter / tabs / "load more" ke liye)
FILES_PAGE = 50
NUMBERS_PAGE = 50
FILE_DETAIL_PAGE = core.FILE_DETAIL_PAGE
FILTERS = [("all", "All"), ("found", "Found"), ("missing", "Not found"), ("multi", "In many files")]
PREVIEW = 8             # har file ke saamne itne numbers seedhe dikhte hain


def full_path(folder, name):
    """Folder + file ka poora path (Windows ke path me \\, warna /)."""
    if not folder:
        return name
    sep = "\\" if "\\" in folder else "/"
    return folder.rstrip("\\/") + sep + name


def _key(token):
    return f"webui:search:{token}"


def _store(user, payload, last10):
    token = secrets.token_urlsafe(10)
    cache.set(_key(token), {"user": user.pk, "payload": payload, "last10": last10}, TTL)
    return token


def _load(request, token):
    data = cache.get(_key(token))
    if not data or data["user"] != request.user.pk:
        return None
    return data


def _expired(request):
    return render(request, "webui/partials/_search_expired.html")


@staff_required
@require_GET
def search_page(request):
    return render(request, "webui/search.html", {
        "active": "search", "min_digits": MIN_DIGITS, "max_numbers": core.MAX_NUMBERS,
        "has_index": FileIndex.objects.exists(), "index_files": FileIndex.objects.count(), **_freshness(),
    })


@staff_required
@require_POST
def search_run(request):
    last10 = request.POST.get("last10", "1") != "0"
    raw = request.POST.get("numbers", "")
    if not raw.strip():
        return message_only("Paste some phone numbers first.", "warn")
    payload = core.bulk_search_payload(raw, last10)
    summary = payload["summary"]
    if not summary["searched"]:
        return message_only(f"No valid numbers found. Numbers shorter than {summary['min_digits']} digits are ignored.", "warn")
    token = _store(request.user, payload, last10)
    first_tab = "files" if payload["files"] else "missing"
    return render(request, "webui/partials/_results.html", {
        "token": token, "s": summary, "tab": first_tab, "payload": payload, "missing_text": "\n".join(payload["not_found"]),
        **_tab_context(request, payload, first_tab, last10, token, params={}),
    })


# ------------------------------------------------------------------ tabs
def _tab_context(request, payload, tab, last10, token, params):
    page = max(1, int(params.get("page") or 1)) if str(params.get("page") or "1").isdigit() else 1
    q = (params.get("q") or "").strip().lower()
    ctx = {"token": token, "tab": tab, "q": q, "page_no": page, "s": payload["summary"]}
    if tab == "files":
        items = payload["files"]
        if q:
            items = [f for f in items if q in f["file"].lower() or q in f["folder"].lower()]
        sort = params.get("sort") or "numbers"
        if sort == "name":
            items = sorted(items, key=lambda f: f["file"].lower())
        elif sort == "newest":
            items = sorted(items, key=lambda f: -f["mtime"])
        shown = items[:page * FILES_PAGE]
        ctx.update(sort=sort, items=[dict(f, preview=f["numbers"][:PREVIEW], more_numbers=max(0, f["numbers_count"] - PREVIEW),
                                     path=full_path(f["folder"], f["file"]))
                                     for f in shown[(page - 1) * FILES_PAGE:]],
                   total=len(items), has_more=len(items) > page * FILES_PAGE, next_page=page + 1)
    elif tab == "numbers":
        flt = params.get("filter") or "all"
        items = payload["results"]
        if flt == "found":
            items = [r for r in items if r["found"]]
        elif flt == "missing":
            items = [r for r in items if not r["found"]]
        elif flt == "multi":
            items = [r for r in items if r["file_count"] > 1]
        if q:
            items = [r for r in items if q in r["number"]]
        window = items[(page - 1) * NUMBERS_PAGE:page * NUMBERS_PAGE]
        window = [dict(r, matches=[dict(m, path=full_path(m["folder"], m["file"])) for m in r["matches"]]) for r in window]
        ctx.update(filter=flt, filters=FILTERS, items=window, total=len(items),
                   has_more=len(items) > page * NUMBERS_PAGE, next_page=page + 1)
    else:
        missing = payload["not_found"]
        shown = missing[:5000]
        ctx.update(items=shown, total=len(missing), text="\n".join(shown))
    return ctx


@staff_required
@require_GET
def search_tab(request, token, tab):
    if tab not in ("files", "numbers", "missing"):
        raise Http404
    data = _load(request, token)
    if data is None:
        return _expired(request)
    ctx = _tab_context(request, data["payload"], tab, data["last10"], token, request.GET)
    if ctx["page_no"] > 1 and tab != "missing":        # "load more": sirf agli rows
        return render(request, f"webui/partials/_{tab}_rows.html", ctx)
    if request.headers.get("HX-Target") in ("files-list", "numbers-list"):    # filter badla: sirf list
        return render(request, f"webui/partials/_{tab}_list.html", ctx)
    return render(request, f"webui/partials/_tab_{tab}.html", ctx)


@staff_required
@require_GET
def search_file_details(request, token, file_id):
    """'View details': is file me search kiye gaye numbers kahan (sheet / row / column) mile."""
    data = _load(request, token)
    if data is None:
        return _expired(request)
    stats = next((f for f in data["payload"]["files"] if f["file_id"] == file_id), None)
    if stats is None:
        raise Http404
    page = int(request.GET.get("page", "1")) if request.GET.get("page", "1").isdigit() else 1
    numbers = stats["numbers"][(page - 1) * FILE_DETAIL_PAGE:page * FILE_DETAIL_PAGE]
    items = core.file_locations(file_id, "\n".join(numbers), data["last10"])
    return render(request, "webui/partials/_file_details.html", {
        "token": token, "file": stats, "items": items, "page_no": page, "next_page": page + 1,
        "has_more": len(stats["numbers"]) > page * FILE_DETAIL_PAGE,
        "capped": stats["numbers_count"] > len(stats["numbers"]),
    })


@staff_required
@require_GET
def search_row(request):
    """'View full row': us row ka poora data file se (modal me)."""
    try:
        file_id, row_no, col = int(request.GET["file_id"]), int(request.GET["row"]), int(request.GET.get("col") or 0)
    except (KeyError, ValueError):
        return message_only("Invalid request.", "bad")
    f, cells, error, _status = core.row_cells(file_id, request.GET.get("sheet", ""), row_no, col)
    return render(request, "webui/partials/_row_detail.html", {
        "file": f or FileIndex.objects.filter(pk=file_id).first(), "cells": cells, "error": error,
        "sheet": request.GET.get("sheet", ""), "row": row_no})


@staff_required
@require_POST
def search_extract(request):
    """File upload (.xlsx/.xls/.csv/.txt) se numbers nikalkar text box bhar do."""
    result, error, _status = core.read_upload_numbers(
        request.FILES.get("file"), bool(request.POST.get("first_col")), request.POST.get("last10", "1") != "0")
    if error:
        return message_only(error, "bad")
    name, unique, duplicates = result
    shown = unique[:core.MAX_NUMBERS]
    notes = [f"Loaded {len(shown):,} numbers from {name}."]
    kind = "ok"
    if duplicates:
        notes.append(f"{duplicates:,} duplicates removed.")
    if len(unique) > core.MAX_NUMBERS:
        kind = "warn"
        notes = [f"{name} has {len(unique):,} numbers. Only the first {core.MAX_NUMBERS:,} are shown here: use "
                 "\"Full Excel report\" to search all of them."]
    if not unique:
        return message_only(f"No phone numbers found in {name}.", "warn")
    response = render(request, "webui/partials/_numbers_box.html", {
        "numbers": "\n".join(shown), "note": " ".join(notes), "note_kind": kind, "big_file": len(unique) > core.MAX_NUMBERS,
        "min_digits": MIN_DIGITS})
    return response
