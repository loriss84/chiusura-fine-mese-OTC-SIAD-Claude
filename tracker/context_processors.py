"""Template context processors."""

from .permissions import is_hq


def user_roles(request):
    """Inject role flags into every template context."""
    if not request.user.is_authenticated:
        return {"user_is_hq": False}
    return {"user_is_hq": is_hq(request.user)}
