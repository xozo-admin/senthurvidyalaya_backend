from django.db.models.signals import post_save
from django.dispatch import receiver
from django.contrib.auth import get_user_model
from teachers.models import Teacher
from students.models import Student
from staff.models import NonTeachingStaff

User = get_user_model()

@receiver(post_save, sender=Teacher)
@receiver(post_save, sender=Student)
@receiver(post_save, sender=NonTeachingStaff)
def sync_user_profile(sender, instance, created, **kwargs):
    """
    Handles TWO things:
    1. CREATION: If new Profile -> Create User Account.
    2. UPDATE: If Profile email changes -> Update User Account email.
    """

    # --- 1. CAPTURE DATA FROM PROFILE ---
    # We extract the data FIRST so we can use it for both Create and Update
    username_val = None
    password_val = None
    email_val = None
    user_type_val = None
    full_name = ""

    if isinstance(instance, Teacher):
        username_val = instance.teacher_id
        password_val = instance.phone
        user_type_val = 'teacher'
        email_val = instance.email
        full_name = instance.name
        
    elif isinstance(instance, Student):
        username_val = instance.student_id
        password_val = instance.father_phone
        user_type_val = 'student'
        # Check both field names just in case
        email_val = getattr(instance, 'student_email', '') or getattr(instance, 'email', '')
        full_name = instance.student_name

    elif isinstance(instance, NonTeachingStaff):
        username_val = instance.staff_id
        password_val = instance.phone
        user_type_val = 'staff'
        email_val = instance.email
        full_name = instance.name

    # Safety Check
    if not username_val:
        return

    # --- 2. UPDATE LOGIC (If User Already Exists) ---
    if instance.user:
        user_account = instance.user
        needs_save = False

        if user_account.username != username_val:
            user_account.username = username_val
            needs_save = True

        # Sync Email
        if user_account.email != email_val:
            user_account.email = email_val
            needs_save = True
            print(f"--- SYNC: Updated email for {username_val} to {email_val} ---")

        password_changed = user_account.phone != str(password_val)
        if password_changed:
            user_account.phone = str(password_val)
            needs_save = True

        if isinstance(instance, Teacher) or (
            isinstance(instance, Student) and password_changed
        ):
            user_account.set_password(str(password_val))
            needs_save = True

        if needs_save:
            user_account.save()
        
        return # Stop here, we are done updating.

    # --- 3. CREATION LOGIC (If User Does Not Exist) ---
    # Split Name
    if full_name:
        parts = full_name.strip().split(' ', 1)
        first_name = parts[0]
        last_name = parts[1] if len(parts) > 1 else ''
    else:
        first_name, last_name = "", ""

    try:
        user, user_created = User.objects.get_or_create(
            username=username_val,
            defaults={
                'email': email_val,
                'user_type': user_type_val,
                'phone': str(password_val),
                'first_name': first_name,
                'last_name': last_name,
                'is_active': True
            }
        )

        if user_created:
            user.set_password(str(password_val))
            user.save()
            print(f"--- NEW ACCOUNT: Created {username_val} | Name: {first_name} ---")

        # Link the Profile to the User
        sender.objects.filter(pk=instance.pk).update(user=user)
        print(f"--- LINK: Linked {user_type_val.upper()} {username_val} to User ID {user.id} ---")

    except Exception as e:
        print(f"Error auto-creating user for {username_val}: {e}")
