from __future__ import annotations

from typing import Any

from django.db.models import Q
from django.db.models import QuerySet

from .models import AcademicYear, School


def get_user_school(user: Any) -> School | None:
    if not getattr(user, "is_authenticated", False):
        return None

    user_type = getattr(user, "user_type", "")

    if user_type == "admin":
        try:
            return user.adminprofile.school
        except Exception:
            return None

    for profile_name in ("teacher_profile", "student_profile", "staff_profile"):
        try:
            profile = getattr(user, profile_name)
            if profile and getattr(profile, "school_id", None):
                return profile.school
        except Exception:
            continue

    return None


def is_super_admin(user: Any) -> bool:
    return bool(
        getattr(user, "is_authenticated", False)
        and getattr(user, "user_type", "") == "super_admin"
    )


def get_requested_school(request: Any) -> School | None:
    user = getattr(request, "user", None)
    if is_super_admin(user):
        school_id = request.query_params.get("school_id")
        if not school_id and hasattr(request, "data"):
            school_id = request.data.get("school_id")
        if school_id:
            return School.objects.filter(pk=school_id).first()
        return None
    return get_user_school(user)


def scope_queryset_for_user(queryset: QuerySet, request: Any, field: str = "school") -> QuerySet:
    user = getattr(request, "user", None)
    requested_school = get_requested_school(request)

    if is_super_admin(user):
        if requested_school:
            return queryset.filter(**{field: requested_school.pk if field == "id" else requested_school})
        return queryset

    if requested_school:
        return queryset.filter(**{field: requested_school.pk if field == "id" else requested_school})

    return queryset.none()


def get_active_academic_year(request: Any = None) -> AcademicYear | None:
    queryset = AcademicYear.objects.filter(is_current=True)
    school = get_requested_school(request) if request else None

    if school:
        return queryset.filter(Q(school=school) | Q(school__isnull=True)).order_by("-school_id").first()

    if request is not None:
        scoped = scope_queryset_for_user(queryset, request)
        return scoped.first()

    return queryset.filter(school__isnull=True).first() or queryset.first()


def get_academic_year_by_identifier(request: Any, identifier: Any) -> AcademicYear | None:
    value = str(identifier or "").strip()
    if not value:
        return None

    queryset = AcademicYear.objects.all()
    school = get_requested_school(request)
    if school:
        queryset = queryset.filter(Q(school=school) | Q(school__isnull=True)).order_by("-school_id")
    else:
        queryset = scope_queryset_for_user(queryset, request)

    if value.isdigit():
        return queryset.filter(pk=int(value)).first()
    return queryset.filter(name=value).first()


def assign_school(instance: Any, request: Any) -> Any:
    school = get_requested_school(request)
    if school and hasattr(instance, "school_id") and not getattr(instance, "school_id", None):
        instance.school = school
        instance.save(update_fields=["school"])
    return instance
