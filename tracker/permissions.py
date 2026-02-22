"""
Permission helpers for Month-End Close Tracker.

Role hierarchy (highest to lowest):
  ADMIN > HQ_PROJECT_OWNER > COUNTRY_CFO > PROCESS_OWNER
"""

from functools import wraps
from django.db import models
from django.core.exceptions import PermissionDenied
from .models import GlobalRole, CountryRole, UserGlobalRole, Membership


# ---------------------------------------------------------------------------
# Role-check helpers
# ---------------------------------------------------------------------------

def get_global_role(user):
    """Return the GlobalRole value for *user*, or None."""
    if not user or not user.is_authenticated:
        return None
    try:
        return user.global_role.role
    except UserGlobalRole.DoesNotExist:
        return None


def is_admin(user):
    return get_global_role(user) == GlobalRole.ADMIN or (user and user.is_superuser)


def is_hq(user):
    role = get_global_role(user)
    return role in (GlobalRole.ADMIN, GlobalRole.HQ_PROJECT_OWNER) or (user and user.is_superuser)


def get_country_role(user, country):
    """Return the best CountryRole for *user* in *country*, or None."""
    if not user or not user.is_authenticated:
        return None
    memberships = Membership.objects.filter(user=user, country=country).values_list("role", flat=True)
    if CountryRole.COUNTRY_CFO in memberships:
        return CountryRole.COUNTRY_CFO
    if CountryRole.PROCESS_OWNER in memberships:
        return CountryRole.PROCESS_OWNER
    return None


def is_cfo_for_country(user, country):
    return is_hq(user) or get_country_role(user, country) == CountryRole.COUNTRY_CFO


def can_update_task(user, task_run):
    """Can the user update status/comment on this task_run?"""
    country = task_run.country
    if is_hq(user):
        return True
    role = get_country_role(user, country)
    if role == CountryRole.COUNTRY_CFO:
        return True
    if role == CountryRole.PROCESS_OWNER:
        return task_run.owner_user == user
    return False


def can_override_due_date(user, task_run):
    """
    Override permissions:
      - PROCESS_OWNER: never
      - COUNTRY_CFO: only own country
      - HQ_PROJECT_OWNER / ADMIN: anywhere
    """
    country = task_run.country
    if is_admin(user) or get_global_role(user) == GlobalRole.HQ_PROJECT_OWNER:
        return True
    role = get_country_role(user, country)
    return role == CountryRole.COUNTRY_CFO


def get_accessible_countries(user):
    """
    Return QuerySet of Country objects accessible to *user*.
    HQ/Admin: all countries.
    Country-scoped: only their countries.
    """
    from .models import Country
    if is_hq(user):
        return Country.objects.all()
    country_ids = Membership.objects.filter(user=user).values_list("country_id", flat=True)
    return Country.objects.filter(id__in=country_ids)


def assert_country_access(user, country):
    """Raise PermissionDenied if user has no access to *country*."""
    if is_hq(user):
        return
    has_access = Membership.objects.filter(user=user, country=country).exists()
    if not has_access:
        raise PermissionDenied(f"No access to country {country.code}")


# ---------------------------------------------------------------------------
# Decorator helpers for views
# ---------------------------------------------------------------------------

def require_hq(view_func):
    """View decorator: requires HQ or Admin role."""
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            from django.contrib.auth.views import redirect_to_login
            return redirect_to_login(request.get_full_path())
        if not is_hq(request.user):
            raise PermissionDenied("HQ or Admin role required.")
        return view_func(request, *args, **kwargs)
    return wrapper


def require_admin(view_func):
    """View decorator: requires Admin role."""
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            from django.contrib.auth.views import redirect_to_login
            return redirect_to_login(request.get_full_path())
        if not is_admin(request.user):
            raise PermissionDenied("Admin role required.")
        return view_func(request, *args, **kwargs)
    return wrapper


# ---------------------------------------------------------------------------
# CountryScopedQuerySet mixin
# ---------------------------------------------------------------------------

class CountryScopedQuerySet(models.QuerySet):
    """
    QuerySet subclass that can restrict results to countries accessible by a user.
    Usage: MyModel.objects.for_user(request.user)
    """
    def for_user(self, user):
        if is_hq(user):
            return self
        country_ids = Membership.objects.filter(user=user).values_list("country_id", flat=True)
        return self.filter(country_id__in=list(country_ids))
