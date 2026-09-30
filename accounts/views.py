from venv import logger

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status, generics
from rest_framework.authtoken.models import Token
from django.contrib.auth import authenticate, login
from django.core.cache import cache
from django.core.exceptions import ObjectDoesNotExist 
from django.utils import timezone

import secrets
import random
from django.core.mail import send_mail
from django.conf import settings

from rest_framework.permissions import AllowAny

from accounts.models import User
from school.models import School
from schooladmin.models import AdminProfile
from .serializers import AdminRegisterSerializer, SchoolRegisterSerializer, SuperAdminRegisterSerializer

# --- Ensure these imports are at the top of accounts/views.py ---
from school.models import AcademicYear
from academics.models import ClassTeacher
from students.models import (
    Enrollment,
    STUDENT_PERMISSION_GROUPS,
    STUDENT_PERMISSION_LABELS,
    default_student_role_permissions,
)
from .external_admin_access import verify_admin_external_access
from teachers.models import TEACHER_PERMISSION_GROUPS, TEACHER_PERMISSION_LABELS, default_teacher_role_permissions
from staff.models import STAFF_PERMISSION_GROUPS, STAFF_PERMISSION_LABELS, default_staff_role_permissions


# ==========================================
#  REGISTRATION VIEWS (Plain Text / HTTPS)
# ==========================================

class AdminRegisterView(generics.GenericAPIView):
    permission_classes = [AllowAny]
    serializer_class = AdminRegisterSerializer

    def post(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        admin = serializer.save()

        return Response({
            "message": "Admin registered successfully",
            "admin_name": admin.first_name 
        }, status=200)


class SuperAdminRegisterView(generics.GenericAPIView):
    permission_classes = [AllowAny]
    serializer_class = SuperAdminRegisterSerializer

    def post(self, request):
        if User.objects.filter(user_type="super_admin").exists():
            return Response({"message": "Super admin already registered"}, status=400)

        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        super_admin = serializer.save()

        return Response({
            "message": "Super admin registered successfully",
            "name": super_admin.first_name,
            "username": super_admin.username,
        }, status=200)


class SchoolRegisterView(generics.GenericAPIView):
    permission_classes = [AllowAny]
    serializer_class = SchoolRegisterSerializer

    def post(self, request):
        admin = User.objects.filter(user_type="admin").first()
        if not admin:
            return Response({"message": "Admin must be registered first"}, status=400)

        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        school = serializer.save()

        AdminProfile.objects.get_or_create(user=admin, defaults={"school": school})

        return Response({
            "message": "School registration completed. Onboarding success!",
            "teachers": school.total_teachers,
            "non_teaching_staff": school.total_non_teaching
        }, status=200)


# ==========================================
#  LOGIN VIEWS (Key Generation / 2FA)
# ==========================================
class LoginView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        username = request.data.get('username')
        password = request.data.get('password')

        if not username or not password:
            return Response({"error": "Please provide username and password"}, status=400)

        username = str(username).strip()
        password = str(password)
        user = authenticate(username=username, password=password)

        if user is None:
            matched_user = User.objects.filter(username__iexact=username).first()
            if matched_user:
                user = authenticate(username=matched_user.username, password=password)

        if user is not None:
            # --- CASE A: ADMIN / SUPER ADMIN (Requires 2FA) ---
            if user.user_type in ('admin', 'super_admin'):
                # if user.user_type == 'admin':
                #     allowed, reason = verify_admin_external_access(user)
                #     if not allowed:
                #         return Response(
                #             {
                #                 "error": "Admin access is not authorized.",
                #                 "reason": reason,
                #             },
                #             status=403,
                #         )

                otp = getattr(settings, "MANUAL_ADMIN_OTP", "6258")
                cache.set(f'otp_{user.username}', otp, timeout=300)

                logger.info(user.email)

                otp_sent_to = "manual"
                message = "OTP required (manual)."

                # --- SEND OTP TO EMAIL (Optional) ---
                if getattr(settings, "OTP_REQUIRED", True):
                    if not user.email:
                        return Response({"error": "No email linked to admin account."}, status=400)

                    masked_email = user.email[0] + "****" + user.email[user.email.find('@'):]

                    try:
                        send_mail(
                            subject="Your Login OTP",
                            message=f"Hello {user.username},\n\nYour login OTP is: {otp}\n\nValid for 5 minutes.",
                            from_email=settings.DEFAULT_FROM_EMAIL,
                            recipient_list=[user.email],
                            fail_silently=False,
                        )
                    except Exception as e:
                        return Response({"error": f"Failed to send OTP email: {str(e)}"}, status=500)

                    otp_sent_to = "email"
                    message = f"OTP sent to {masked_email}"

                return Response(
                    {
                        "message": message,
                        "status": "2FA_REQUIRED",
                        "user_type": user.user_type,
                        "username": user.username,
                        "otp_sent_to": otp_sent_to,
                    },
                    status=200,
                )

            # --- CASE B: OTHERS (Direct Login) ---
            else:
                return self.finalize_login(user)

        else:
            return Response({"error": "Invalid Credentials"}, status=401)

    def finalize_login(self, user):
        """Helper function to generate encryption keys and return success response"""
        user.last_login = timezone.now()
        # 1. Generate AES Session Key
        new_session_key = secrets.token_hex(32)
        user.session_key = new_session_key
        user.save()

        # 2. Get/Create Token
        token, _ = Token.objects.get_or_create(user=user)

        # 3. Dynamic Pathing
        auth_code = ""
        redirect_path = ""
        
        if user.user_type == 'student':
            auth_code = f"STU{user.username}"
            redirect_path = f"/student/profile/{user.username}"
        elif user.user_type == 'teacher':
            auth_code = f"TCH{user.username}"
            redirect_path = f"/teacher/dashboard/{user.username}"
        elif user.user_type == 'staff':
            auth_code = f"STF{user.username}"
            redirect_path = f"/staff/dashboard/{user.username}"
        elif user.user_type == 'admin':
             auth_code = f"ADM{user.username}"
             redirect_path = "/admin/dashboard"
        elif user.user_type == 'super_admin':
             auth_code = f"SUP{user.username}"
             redirect_path = "/admin"

        # 4. Prepare Base Response
        response_data = {
            "status": 200,
            "message": "Login Successful",
            "user_type": user.user_type,
            "auth_code": auth_code,
            "token": token.key,
            "session_key": new_session_key, 
            "redirect_to": redirect_path
        }

        school = None
        if user.user_type == 'admin':
            try:
                school = user.adminprofile.school
            except Exception:
                school = None
        elif user.user_type == 'teacher' and hasattr(user, 'teacher_profile'):
            school = user.teacher_profile.school
        elif user.user_type == 'student' and hasattr(user, 'student_profile'):
            school = user.student_profile.school
        elif user.user_type == 'staff' and hasattr(user, 'staff_profile'):
            school = user.staff_profile.school

        if school:
            response_data.update({
                "school_id": school.id,
                "school_name": school.name,
                "institution_id": school.institution_id,
                "institution_name": school.institution.name if school.institution else None,
            })
        elif user.user_type == 'super_admin':
            schools = School.objects.select_related('institution').filter(is_active=True).order_by('name')
            response_data.update({
                "school_id": None,
                "school_name": "All Schools",
                "institution_id": None,
                "institution_name": "Institution Overview",
                "can_access_all_schools": True,
                "available_schools": [
                    {
                        "id": school.id,
                        "name": school.name,
                        "code": school.code,
                        "institution_id": school.institution_id,
                        "institution_name": school.institution.name if school.institution else None,
                    }
                    for school in schools
                ],
            })

        # --- LOGIC A: TEACHER (YEAR-SAFE) ---
        if user.user_type == 'teacher':
            response_data['teacher_type'] = "Subject Teacher"
            response_data['class'] = "N/A"
            response_data['section'] = "N/A"
            response_data['role_permissions'] = default_teacher_role_permissions()
            response_data['permission_groups'] = TEACHER_PERMISSION_GROUPS
            response_data['permission_labels'] = TEACHER_PERMISSION_LABELS

            if hasattr(user, 'teacher_profile'):
                response_data['role_permissions'] = user.teacher_profile.normalized_role_permissions()
                try:
                    active_year = AcademicYear.objects.get(is_current=True)
                    ct_record = ClassTeacher.objects.filter(
                        teacher=user.teacher_profile, 
                        academic_year=active_year
                    ).select_related('section__standard').first()

                    if ct_record:
                        response_data['teacher_type'] = "Class Teacher"
                        response_data['class'] = ct_record.section.standard.name
                        response_data['section'] = ct_record.section.name
                except (AcademicYear.DoesNotExist, ObjectDoesNotExist):
                    pass

        # --- LOGIC B: STUDENT (YEAR-SAFE ENROLLMENT) ---
        elif user.user_type == 'student':
            response_data['class'] = "Not Enrolled"
            response_data['section'] = "N/A"
            response_data['student_role_permissions'] = default_student_role_permissions()
            response_data['student_permission_groups'] = STUDENT_PERMISSION_GROUPS
            response_data['student_permission_labels'] = STUDENT_PERMISSION_LABELS

            if hasattr(user, 'student_profile'):
                response_data['student_role_permissions'] = user.student_profile.normalized_role_permissions()
                try:
                    active_year = AcademicYear.objects.get(is_current=True)
                    enrollment = Enrollment.objects.filter(
                        student=user.student_profile, 
                        academic_year=active_year,
                        is_active=True
                    ).select_related('standard', 'section').first()

                    if enrollment:
                        response_data['class'] = enrollment.standard.name
                        response_data['section'] = enrollment.section.name if enrollment.section else "Not Assigned"
                except (AcademicYear.DoesNotExist, ObjectDoesNotExist):
                    pass

        # --- LOGIC C: STAFF (STATIC ROLE) ---
        elif user.user_type == 'staff':
            if hasattr(user, 'staff_profile'):
                response_data['staff_role'] = user.staff_profile.role
                response_data['staff_role_permissions'] = user.staff_profile.normalized_role_permissions()
            else:
                response_data['staff_role'] = "General Staff"
                response_data['staff_role_permissions'] = default_staff_role_permissions()
            response_data['staff_permission_groups'] = STAFF_PERMISSION_GROUPS
            response_data['staff_permission_labels'] = STAFF_PERMISSION_LABELS

        return Response(response_data, status=200)


class VerifyOTPView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        username = request.data.get('username')
        otp_input = request.data.get('otp')

        if not username or not otp_input:
            return Response({"error": "Username and OTP required"}, status=400)

        saved_otp = cache.get(f'otp_{username}')

        if saved_otp and str(saved_otp) == str(otp_input):
            try:
                user = User.objects.get(username=username)
                cache.delete(f'otp_{username}')
                login_view = LoginView()
                login_view.request = request
                return login_view.finalize_login(user)
            except User.DoesNotExist:
                return Response({"error": "User not found"}, status=404)
        else:
            return Response({"error": "Invalid or Expired OTP"}, status=400)

