"""Pehli baar chalane par account banane ka page (installed app ke liye: customer ko 'createsuperuser' nahi karna padta).

Licensed (bechne wali) app me ye page customer se naam + email + password leta hai. Naam + email seller ke server par
jata hai (admin panel me 'Waiting for key' ki row), password SIRF is PC par rehta hai (isse app khulta hai). Key seller email karta hai.
Development copy (license check band) me purana simple form: username + password.
"""
from django.contrib.auth import get_user_model, login, password_validation
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from licensing import service


def _finish(request, User, username, email, password):
    user = User.objects.create_superuser(username=username, email=email, password=password)
    login(request, user, backend="django.contrib.auth.backends.ModelBackend")
    return redirect("/app/")


def _legacy(request, User):
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
            return _finish(request, User, username, "", password)
    return render(request, "setup.html", {"errors": errors, "username": username, "account_mode": False})


def _account(request, User):
    posted = request.method == "POST"
    errors = []
    form = {"name": request.POST.get("name", "").strip(), "email": request.POST.get("email", "").strip().lower()} if posted else {}
    if posted:
        password, confirm = request.POST.get("password", ""), request.POST.get("confirm", "")
        try:
            validate_email(form["email"])
        except ValidationError:
            errors.append("Please enter a valid email address.")
        if not form["name"]:
            errors.append("Please enter your name.")
        if password != confirm:
            errors.append("The two passwords do not match.")
        if not errors:
            try:
                password_validation.validate_password(password, User(username=form["email"]))
            except ValidationError as exc:
                errors.extend(exc.messages)
        if not errors:
            result = service.register(form["name"], form["email"])       # password server ko nahi jata
            if result.ok:
                return _finish(request, User, form["email"][:150], form["email"], password)
            errors.append(result.message)
    return render(request, "setup.html", {"errors": errors, "form": form, "account_mode": True})


@require_http_methods(["GET", "POST"])
def setup(request):
    User = get_user_model()
    if User.objects.exists():                 # account ban chuka: ye page band
        return redirect("/app/")
    return _account(request, User) if service.enforced() else _legacy(request, User)
