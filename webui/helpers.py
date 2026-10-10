"""Naye UI ke chhote madadgaar: login check, htmx ke jawab (toast / redirect), ETA ka hisaab."""
from functools import wraps
from urllib.parse import quote

from django.http import HttpResponse
from django.shortcuts import redirect
from django.template.loader import render_to_string
from django.urls import reverse


def is_htmx(request):
    return request.headers.get("HX-Request") == "true"


def wants_partial(request, target):
    """Sirf wahi hissa chahiye (htmx ne us element ko target kiya hai), poora page nahi."""
    return is_htmx(request) and request.headers.get("HX-Target") == target and request.headers.get("HX-Boosted") != "true"


def htmx_redirect(url):
    response = HttpResponse(status=204)
    response["HX-Redirect"] = url
    return response


def staff_required(view):
    """Login kiya hua staff hi. Nahi toh /app/login/ (htmx request ho toh poora page badalkar wahin bhejta hai)."""
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        user = request.user
        if user.is_authenticated and user.is_active and user.is_staff:
            return view(request, *args, **kwargs)
        login_url = f"{reverse('webui:login')}?next={quote(request.get_full_path())}"
        return htmx_redirect(login_url) if is_htmx(request) else redirect(login_url)
    return wrapper


def toast(message, kind="info"):
    """Chhota notification (out-of-band): kisi bhi htmx jawab ke saath jod do, ye #toasts me jud jata hai."""
    return render_to_string("webui/partials/_toast.html", {"message": message, "kind": kind})


def with_toast(response, message, kind="info"):
    response.content += toast(message, kind).encode()
    return response


def message_only(message, kind="info", status=200):
    """Sirf notification, page ka koi hissa nahi badalta (HX-Reswap: none)."""
    response = HttpResponse(toast(message, kind), status=status)
    response["HX-Reswap"] = "none"
    return response


# ------------------------------------------------------------------ scan ETA / time
def fmt_clock(seconds):
    seconds = int(seconds or 0)
    return f"{seconds // 60}:{seconds % 60:02d}"


def fmt_eta(seconds):
    if seconds < 45:
        return "less than a minute"
    minutes = round(seconds / 60)
    if minutes < 60:
        return f"~{minutes} min"
    hours, rest = divmod(minutes, 60)
    return f"~{hours} h" + (f" {rest} min" if rest else "")


def scan_progress_view(status):
    """scan_status() dict se progress bar ka data: percent, 'kitna baaki' ka text, indeterminate (abhi gin rahe hain)."""
    done = status["files_indexed"] + status["files_skipped"] + status["files_failed"]
    if status["files_total"] is None:
        counted = status["files_counted"]
        text = "Counting files" + (f"... {counted:,} Excel files found so far" if counted else "...")
        return {"percent": 0, "indeterminate": True, "text": text, "done": done}
    total = max(status["files_total"], done)    # gin-ti aur scan ke beech file badle toh bar 100% se upar na jaye
    percent = min(100.0, done / total * 100) if total else 100.0
    text = f"{done:,} of {total:,} files"
    if done >= 3 and status["elapsed"] >= 3 and done < total:
        text += f" · {fmt_eta((total - done) / (done / status['elapsed']))} left"
    return {"percent": round(percent, 1), "indeterminate": False, "text": text, "done": done}
