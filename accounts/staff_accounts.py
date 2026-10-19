from django.contrib.auth import get_user_model

User = get_user_model()


def provision_staff_login(staff_member):
    """Ensure a staff profile has a usable login; return its User."""
    username = str(staff_member.staff_id or "").strip()
    phone = str(staff_member.phone or "")
    if not username:
        raise ValueError("Staff ID is required to create a login.")
    if not phone.strip():
        raise ValueError("A phone number is required to create the initial password.")

    user = staff_member.user
    if user is None:
        user = User.objects.filter(username__iexact=username).first()
        if user and user.user_type != "staff":
            raise ValueError("This staff ID is already used by a different account type.")
        if user and hasattr(user, "staff_profile"):
            if user.staff_profile.pk != staff_member.pk:
                raise ValueError("This staff ID is already linked to another staff record.")
        if user is None:
            user = User(username=username, user_type="staff")

    parts = str(staff_member.name or "").strip().split(" ", 1)
    user.username = username
    user.user_type = "staff"
    user.phone = phone
    user.email = staff_member.email or ""
    user.first_name = parts[0] if parts else ""
    user.last_name = parts[1] if len(parts) > 1 else ""
    user.is_active = True
    user.set_password(phone)
    user.save()

    if staff_member.user_id != user.pk:
        type(staff_member).objects.filter(pk=staff_member.pk).update(user=user)
        staff_member.user = user

    return user
