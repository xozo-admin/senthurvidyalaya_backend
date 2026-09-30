import logging
from typing import Any

import requests
from django.conf import settings

from school.models import School

logger = logging.getLogger(__name__)


def resolve_school_name(user) -> str:
    try:
        if hasattr(user, "adminprofile") and user.adminprofile.school:
            return user.adminprofile.school.name or ""
    except Exception:
        logger.warning("Could not resolve school name from admin profile.", exc_info=True)

    school = School.objects.first()
    return school.name if school else ""


def build_admin_access_payload(user) -> dict[str, Any]:
    return {
        "username": user.username,
        "email": user.email or "",
        "user_id": user.id,
        "school_name": resolve_school_name(user),
    }


def verify_admin_external_access(user) -> tuple[bool, str]:
    api_url = getattr(settings, "ADMIN_ACCESS_API_URL", "")
    if not api_url:
        return True, "External admin access check is not configured."

    headers = {"Content-Type": "application/json"}
    api_key = getattr(settings, "ADMIN_ACCESS_API_KEY", "")
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    try:
        response = requests.post(
            api_url,
            json=build_admin_access_payload(user),
            headers=headers,
            timeout=getattr(settings, "ADMIN_ACCESS_API_TIMEOUT", 5),
        )
        response.raise_for_status()
        data = response.json() if response.content else {}

        if data.get("allowed") is True:
            return True, str(data.get("reason") or "Admin access allowed.")

        return False, str(data.get("reason") or "Admin access is not authorized.")
    except Exception as exc:
        logger.warning("External admin access check failed: %s", exc)
        if getattr(settings, "ADMIN_ACCESS_FAIL_CLOSED", True):
            return False, "External admin access check failed."
        return True, "External admin access check skipped after failure."
