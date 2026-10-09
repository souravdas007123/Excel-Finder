"""Pehli baar chalane par admin account banane ka page (installed app ke liye: customer ko 'createsuperuser' nahi karna padta)."""
from django.contrib.auth import get_user_model, login, password_validation
from django.core.exceptions import ValidationError
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods


@require_http_methods(["GET", "POST"])
def setup(request):
    User = get_user_model()
    if User.objects.exists():                 # account ban chuka: ye page band
        return redirect("/admin/")
    errors = []
    username = (request.POST.get("username", "") if request.method == "POST" else "admin").strip()
    if request.method == "POST":
        password, confirm = request.POST.get("password", ""), request.POST.get("confirm", "")
        if not username or len(username) > 150 or any(c.isspace() for c in username):
            errors.append("Choose a username without spaces (up to 150 characters).")
        if password != confirm:
            errors.append("The two passwords do not match.")
        if not errors:
            try:
                password_validation.validate_password(password, User(username=username))
            except ValidationError as exc:
                errors.extend(exc.messages)
        if not errors:
            user = User.objects.create_superuser(username=username, email="", password=password)
            login(request, user, backend="django.contrib.auth.backends.ModelBackend")
            return redirect("/admin/")
    return render(request, "setup.html", {"errors": errors, "username": username})
