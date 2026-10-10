"""Scan page: shuru / rok / progress (har second htmx se) / folder chunna / index saaf karna."""
from django.http import HttpResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from fileindex import services as core
from fileindex.drives import get_scan_locations
from fileindex.models import FileIndex, ScanTask
from fileindex.scanner import cancel_event, scan_lock

from .helpers import message_only, scan_progress_view, staff_required
from .views import _freshness, numbers_count


def _running_task():
    return ScanTask.objects.filter(status="Running").order_by("-id").first() if scan_lock.locked() else None


def _progress(request, task, stopping=False):
    status = core.scan_status(task)
    running = task.status == "Running"
    return render(request, "webui/partials/_scan_progress.html", {
        "task": task, "s": status, "running": running, "stopping": stopping or (running and cancel_event.is_set()),
        "bar": scan_progress_view(status) if running else None,
    })


@staff_required
@require_GET
def scan_page(request):
    last = ScanTask.objects.exclude(status="Running").order_by("-id").first()
    running = _running_task()
    return render(request, "webui/scan.html", {
        "active": "scan",
        "locations": [(key, loc["label"]) for key, loc in get_scan_locations().items()],
        "allow_custom": core.custom_path_allowed(request.user),
        "can_clear": request.user.has_perm("fileindex.delete_fileindex"),
        "running": running,
        "last": last,
        "files": FileIndex.objects.count(),
        "numbers": numbers_count() if FileIndex.objects.exists() else 0,
        **_freshness(),
    })


@staff_required
@require_POST
def scan_start(request):
    task_id, error, status = core.start_scan(request.user, request.POST.get("location", ""), request.POST.get("custom_path", ""))
    if error:
        return message_only(error, "bad", status=200)
    return _progress(request, ScanTask.objects.get(pk=task_id))


@staff_required
@require_GET
def scan_status(request):
    """Har second ye partial aata hai. Scan khatam ho toh polling band (naya partial me hx-trigger nahi) aur page ke stats refresh."""
    try:
        task = ScanTask.objects.filter(pk=int(request.GET.get("task", ""))).first()
    except ValueError:
        task = None
    if task is None:
        return HttpResponse("")
    response = _progress(request, task)
    if task.status != "Running":
        response["HX-Trigger"] = "indexChanged"
    return response


@staff_required
@require_POST
def scan_stop(request):
    ok, error = core.stop_scan()
    task = _running_task() or ScanTask.objects.order_by("-id").first()
    if not ok or task is None:
        return message_only(error or "No scan is running", "warn")
    return _progress(request, task, stopping=True)


@staff_required
@require_GET
def scan_browse(request):
    """Folder chunne ki list (sirf superuser). Path par click karne par andar ki list aati hai."""
    if not core.custom_path_allowed(request.user):
        return message_only("Choosing a specific folder is not allowed for your account.", "bad")
    data, error, _status = core.browse_folders(request.GET.get("path", ""))
    return render(request, "webui/partials/_folder_browser.html", {"data": data, "error": error})


@staff_required
@require_http_methods(["GET", "POST"])
def scan_clear(request):
    """GET: 'sach me hatana hai?' ka dialog. POST: index saaf."""
    if request.method == "GET":
        if not request.user.has_perm("fileindex.delete_fileindex"):
            return message_only("You do not have permission to clear the index.", "bad")
        return render(request, "webui/partials/_clear_confirm.html", {"files": FileIndex.objects.count()})
    removed, error, _status = core.clear_index(request.user)
    if error:
        return message_only(error, "bad")
    response = HttpResponse(status=204)
    response["HX-Trigger"] = '{"indexChanged": true, "closeModal": true, "toast": {"message": "Index cleared (%d files removed). Your Excel files were not touched.", "kind": "ok"}}' % removed
    return response

