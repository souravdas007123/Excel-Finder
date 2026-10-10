from datetime import datetime

from django import template
from django.utils import timezone

register = template.Library()


@register.filter
def filesize(value):
    try:
        size = float(value)
    except (TypeError, ValueError):
        return ""
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024


@register.filter
def timeago(value):
    if not value:
        return "never"
    seconds = max(0, int((timezone.now() - value).total_seconds()))
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{seconds // 60} min ago"
    if seconds < 86400:
        return f"{seconds // 3600} h ago"
    days = seconds // 86400
    return f"{days} day{'s' if days != 1 else ''} ago" if days < 60 else value.strftime("%d %b %Y")


@register.filter
def comma(value):
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return value


@register.filter
def clock(seconds):
    seconds = int(seconds or 0)
    return f"{seconds // 60}:{seconds % 60:02d}"


@register.filter
def mtime(value):
    """FileIndex.file_mtime (epoch seconds) ko tareekh me."""
    if not value:
        return ""
    return datetime.fromtimestamp(value).strftime("%d %b %Y, %H:%M")


@register.filter
def folder_of(path):
    import os
    return os.path.dirname(path or "")


from django.utils.html import format_html  # noqa: E402


@register.simple_tag
def icon(name, size=18):
    """Chhota SVG icon (base.html ke sprite se): {% icon "search" %}"""
    return format_html('<svg class="icon" width="{0}" height="{0}" aria-hidden="true"><use href="#i-{1}"></use></svg>', size, name)


@register.simple_tag
def navattrs():
    """Link ko 'boost' karo: page ka sirf main hissa badalta hai (sidebar, toast, modal wahin rehte hain)."""
    return mark_safe('hx-boost="true" hx-target="#page" hx-select="#page" hx-select-oob="#nav" hx-swap="outerHTML"')


from django.utils.safestring import mark_safe  # noqa: E402
