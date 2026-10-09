from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.shortcuts import redirect
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from . import update_check


def _back(request):
    target = request.POST.get("next", "")
    ok = url_has_allowed_host_and_scheme(target, allowed_hosts={request.get_host()}, require_https=request.is_secure())
    return redirect(target if ok else "/admin/")


@staff_member_required
@require_POST
def check(request):
    if not update_check.enabled():
        messages.info(request, "Update checking is not set up in this copy.")
    else:
        result = update_check.check_now()
        (messages.success if result.ok else messages.error)(request, result.message)
    return _back(request)


@staff_member_required
@require_POST
def dismiss(request):
    update_check.dismiss(request.POST.get("version", ""))
    return _back(request)
