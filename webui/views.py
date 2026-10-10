"""Naya UI (/app/): login, dashboard, indexed files, scan history. Search / scan / license alag files me."""
import re

from django.conf import settings
from django.contrib.auth import login as auth_login, logout as auth_logout
from django.contrib.auth.forms import AuthenticationForm
from django.core.cache import cache
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from fileindex.excel_parser import match_key
from fileindex.models import FileIndex, NumberIndex, ScanTask
from fileindex.scanner import MAX_FAILURES_STORED, scan_lock
from licensing import service as license_service
from licensing.models import LicenseState

from .helpers import staff_required, wants_partial

FILES_PER_PAGE = 25


def _freshness():
    """Index kitna purana hai (SCAN_STALE_DAYS se purana ho toh chetavni)."""
    last = (ScanTask.objects.filter(status="Completed").order_by("-id").values_list("created_at", flat=True).first())
    stale_days = getattr(settings, "SCAN_STALE_DAYS", 7)
    return {"last_scan": last, "stale_days": stale_days,
            "stale": bool(last and (timezone.now() - last).days >= stale_days)}


def numbers_count():
    """Kul indexed numbers. Badi table par COUNT dheema ho sakta hai, isliye thodi der yaad rakhte hain."""
    key = "webui:numbers_count"
    value = cache.get(key)
    if value is None:
        value = NumberIndex.objects.count()
        cache.set(key, value, 60)
    return value


# ------------------------------------------------------------------ login / logout
@require_http_methods(["GET", "POST"])
def login_view(request):
    if request.user.is_authenticated and request.user.is_staff:
        return redirect("webui:dashboard")
    form = AuthenticationForm(request, data=request.POST or None)
    form.fields["username"].widget.attrs.update(autofocus=True, autocomplete="username", placeholder="Email or username")
    form.fields["password"].widget.attrs.update(autocomplete="current-password", placeholder="Password")
    nxt = request.POST.get("next") or request.GET.get("next") or ""
    if request.method == "POST" and form.is_valid():
        if not form.get_user().is_staff:
            form.add_error(None, "This account cannot use the app.")
        else:
            auth_login(request, form.get_user())
            safe = url_has_allowed_host_and_scheme(nxt, allowed_hosts={request.get_host()}, require_https=request.is_secure())
            return redirect(nxt if safe and nxt else "webui:dashboard")
    return render(request, "webui/login.html", {"form": form, "next": nxt})


@require_POST
def logout_view(request):
    auth_logout(request)
    return redirect("webui:login")


# ------------------------------------------------------------------ dashboard
@staff_required
@require_GET
def dashboard(request):
    running = ScanTask.objects.filter(status="Running").order_by("-id").first() if scan_lock.locked() else None
    return render(request, "webui/dashboard.html", {
        "active": "dashboard",
        "recent": ScanTask.objects.order_by("-id")[:5],
        "running": running,
        "has_index": FileIndex.objects.exists(),
        "usage": LicenseState.get() if license_service.enforced() else None,
    })


@staff_required
@require_GET
def dashboard_stats(request):
    """Upar ke 4 cards (htmx se load hote hain: badi database par COUNT dheema ho toh page ruke nahi)."""
    last_task = ScanTask.objects.exclude(status="Running").order_by("-id").first()
    context = {"files": FileIndex.objects.count(), "numbers": numbers_count(), "last_task": last_task,
               "failed": last_task.files_failed if last_task else 0, **_freshness()}
    return render(request, "webui/partials/_dashboard_stats.html", context)


# ------------------------------------------------------------------ indexed files
def _file_query(request):
    term = request.GET.get("q", "").strip()
    qs = FileIndex.objects.all().order_by("-last_scanned", "id")
    if term:
        digits = {re.sub(r"\D", "", t) for t in re.split(r"[,;\s]+", term)}
        digits.discard("")
        cond = Q(file_name__icontains=term) | Q(file_path__icontains=term)
        if digits:     # numbers se bhi dhundho (aakhri 10 digits)
            keys = {match_key(d) for d in digits}
            cond |= Q(pk__in=NumberIndex.objects.filter(match_key__in=keys).values("file_id"))
        qs = qs.filter(cond)
    return term, qs


@staff_required
@require_GET
def files(request):
    term, qs = _file_query(request)
    page = Paginator(qs, FILES_PER_PAGE).get_page(request.GET.get("page"))
    context = {"active": "files", "q": term, "page": page, "total_files": FileIndex.objects.count()}
    template = "webui/partials/_files_table.html" if wants_partial(request, "files-table") else "webui/files.html"
    return render(request, template, context)


# ------------------------------------------------------------------ scan history
@staff_required
@require_GET
def history(request):
    page = Paginator(ScanTask.objects.order_by("-id"), 20).get_page(request.GET.get("page"))
    template = "webui/partials/_history_table.html" if wants_partial(request, "history-table") else "webui/history.html"
    return render(request, template, {"active": "history", "page": page})


@staff_required
@require_GET
def history_detail(request, task_id):
    task = get_object_or_404(ScanTask, pk=task_id)
    failures = list(task.failures.order_by("id")[:MAX_FAILURES_STORED])
    return render(request, "webui/partials/_history_detail.html", {
        "task": task, "failures": failures, "more": max(0, task.files_failed - len(failures))})
