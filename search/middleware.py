from django.contrib.auth import get_user_model
from django.shortcuts import redirect

_users_exist = False


def reset_first_run_cache():
    global _users_exist
    _users_exist = False


class FirstRunSetupMiddleware:
    """Koi user nahi hai toh har page /setup/ par bhejo (login ho hi nahi sakta). Account banne ke baad kuch nahi karta."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        global _users_exist
        if not _users_exist:
            _users_exist = get_user_model().objects.exists()
            if not _users_exist and not request.path.startswith(("/setup/", "/static/")):
                return redirect("/setup/")
        return self.get_response(request)


class UpdateCheckMiddleware:
    """Login kiye hue staff ke admin page kholne par roz ek baar (alag thread me) 'naya version?' dekhta hai."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if request.method == "GET" and request.path.startswith(("/admin/", "/app/")) and user is not None \
                and user.is_authenticated and user.is_staff:
            from . import update_check
            update_check.maybe_background_check()
        return self.get_response(request)
