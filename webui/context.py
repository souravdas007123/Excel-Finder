from licensing import service
from search.version import VERSION


def shell(request):
    """Har naye-UI page ke side menu ke liye: version, license chip, expiry ki chetavni."""
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated or not request.path.startswith("/app/"):
        return {}
    status = service.current_status()
    if not service.enforced():
        chip = {"tone": "off", "text": "Development copy", "sub": "License checks are off"}
    elif status.ok:
        sub = (f"{status.days_left} day{'s' if status.days_left != 1 else ''} left" if status.days_left is not None
               else ("Never expires" if status.license_type == "lifetime" else ""))
        chip = {"tone": "warn" if status.warn else "ok", "text": status.type_label or "Active", "sub": sub}
    elif status.code == "unlicensed":
        chip = {"tone": "warn", "text": "License key needed", "sub": "Ask the seller for a key"}
    else:
        chip = {"tone": "bad", "text": "License problem", "sub": status.message[:60]}
    return {"app_version": VERSION, "license_chip": chip,
            "license_warn": status.warn if service.enforced() and status.ok else ""}
