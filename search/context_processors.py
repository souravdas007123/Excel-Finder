from . import update_check


def update_notice(request):
    """Har admin page par 'naya version' banner ka data (sirf login kiye hue staff ko)."""
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated or not user.is_staff:
        return {}
    return {"update_notice": update_check.current_notice()}
