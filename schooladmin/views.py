import csv
import io
import os
import zipfile
from datetime import datetime
from django.core.files.base import ContentFile
from django.db import transaction
from django.contrib.auth.hashers import make_password
from announcements.models import Announcement, CommonAnnouncement, StaffAnnouncement, TeacherAnnouncement
from inventory.models import InventoryItem, StockLog
from rest_framework.views import APIView
from rest_framework import generics
from rest_framework.response import Response
from rest_framework import status
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAuthenticated

# --- IMPORTS FROM YOUR APPS ---
from accounts.models import User
from staff.permissions import (
    IsOwnerAdminOrAdminStaff,
    IsOwnerAdminOrAdminStaffOrHostelWarden,
    IsOwnerAdminOrFinanceAdminStaff,
)
from staff_work.models import WorkAssignment
from .permissions import IsAdmin, IsSuperAdmin
from .serializers import AdminProfileSerializer, AdminUserSerializer
from accounts.serializers import AdminCreateSerializer

# Models
from students.models import Student, Enrollment
from teachers.models import Teacher, TeacherAllocation
from staff.models import NonTeachingStaff
from school.models import AcademicYear  # <--- NEW IMPORT
from school.tenant import get_active_academic_year, get_requested_school, is_super_admin, scope_queryset_for_user
from timetable.models import Substitution, TimetableSlot  # <--- ADD THIS

# --- UPDATED: Import ClassTeacher from academics ---
from academics.models import Section, Standard, ClassTeacher 

# Serializers
from students.serializers import StudentProfileSerializer
from teachers.serializers import TeacherProfileSerializer
from staff.serializers import StaffProfileSerializer

# Model Imports
from teachers.models import Teacher
from staff.models import NonTeachingStaff
from attendance.models import Attendance, TeacherAttendance, StaffAttendance
from leave_management.models import LeaveRequest
from schooladmin.permissions import IsAdmin
from django.utils import timezone
from holidays.models import Holiday
import logging
# Model Imports
from transport.models import TransportAttendance, Vehicle, TransportAllocation, Route, TransportExpenseProof
from django.db.models import Count, F, Prefetch, Q, Sum
from exams.models import ExamSchedule, MarkChangeRequest, StudentMark
from rest_framework.parsers import MultiPartParser, FormParser
from rest_framework import status
from rest_framework.exceptions import ValidationError

logger = logging.getLogger(__name__)

def parse_date(date_str):
    """
    Convert various date formats into Python date object.
    Returns None if invalid or empty.
    """
    if not date_str:
        return None

    date_str = str(date_str).strip()

    formats = [
        "%Y-%m-%d",      # 2010-06-12
        "%d-%m-%Y",      # 12-06-2010
        "%d/%m/%Y",      # 12/06/2010
        "%m/%d/%Y",      # 06/12/2010
        "%d.%m.%Y",      # 12.06.2010
        "%d %b %Y",      # 12 Jun 2010
        "%d %B %Y",      # 12 June 2010
        "%Y/%m/%d",      # 2010/06/12
    ]

    for fmt in formats:
        try:
            return datetime.strptime(date_str, fmt).date()
        except ValueError:
            continue

    raise ValueError(
        f"Invalid date format '{date_str}'. "
        "Supported formats: YYYY-MM-DD, DD-MM-YYYY, DD/MM/YYYY, DD Mon YYYY"
    )

def normalize_csv_key(key):
    return str(key or "").strip().lower().replace(" ", "_").replace("-", "_")

def normalize_csv_row(row):
    return {
        normalize_csv_key(key): str(value).strip() if value is not None else ""
        for key, value in row.items()
    }

def csv_value(row, *keys):
    for key in keys:
        value = row.get(normalize_csv_key(key), "")
        if value != "":
            return value
    return ""

def normalize_accommodation(value):
    raw_text = str(value or "").strip().lower()
    raw = raw_text.replace("-", "_").replace(" ", "_")
    compact = raw.replace("_", "")
    if raw in {"", "none", "na", "n/a", "not_applicable"} or compact in {"none", "na", "notapplicable"}:
        return None
    if raw in {"day_scholar", "dayscholar", "day", "day_student", "day_boarder", "non_hosteller"} or compact in {
        "dayscholar",
        "daystudent",
        "dayboarder",
        "nonhosteller",
        "no",
        "false",
    }:
        return "day_scholar"
    if raw in {"hosteller", "hostler", "hostel", "boarding", "boarder", "residential"} or compact in {
        "hosteller",
        "hostler",
        "hostel",
        "boarding",
        "boarder",
        "residential",
        "yes",
        "true",
    }:
        return "hosteller"
    raise ValueError("Accommodation must be day_scholar/day scholar or hosteller/hostel.")

def csv_accommodation_value(row):
    return csv_value(
        row,
        "accommodation",
        "accomodation",
        "student_accommodation",
        "student_accomodation",
        "accommodation_type",
        "accomodation_type",
        "hostel",
        "hosteller",
        "boarding",
    )

# ==========================================
# 1. ADMIN PROFILE (View Own Details)
# ==========================================
class AdminProfileView(APIView):
    permission_classes = [IsAuthenticated, IsAdmin]

    def get(self, request):
        serializer = AdminProfileSerializer(request.user)
        return Response({"status": 200, "data": serializer.data}, status=status.HTTP_200_OK)


class AdminSidebarCountsView(APIView):
    permission_classes = [IsAuthenticated, IsAdmin]

    def get(self, request):
        active_year = get_active_academic_year(self.request)

        pending_leave_requests = LeaveRequest.objects.filter(
            user_type__in=['staff', 'teacher'],
            status='Pending'
        ).count()

        pending_mark_approvals_query = MarkChangeRequest.objects.filter(status='PENDING')
        if active_year:
            pending_mark_approvals_query = pending_mark_approvals_query.filter(
                enrollment__academic_year=active_year
            )

        pending_mark_approvals = pending_mark_approvals_query.count()

        live_buses = Route.objects.filter(is_active=True).count()

        return Response({
            "status": 200,
            "data": {
                "pending_leave_requests": pending_leave_requests,
                "pending_mark_approvals": pending_mark_approvals,
                "live_buses": live_buses,
            }
        }, status=status.HTTP_200_OK)


class SuperAdminAdminUserListCreateView(APIView):
    permission_classes = [IsAuthenticated, IsSuperAdmin]

    def get(self, request):
        admins = User.objects.filter(user_type='admin').select_related('adminprofile__school').order_by('first_name', 'username')
        school = get_requested_school(request)
        if school:
            admins = admins.filter(adminprofile__school=school)
        serializer = AdminUserSerializer(admins, many=True)
        return Response({"status": 200, "data": serializer.data}, status=status.HTTP_200_OK)

    def post(self, request):
        serializer = AdminCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        admin = serializer.save()
        return Response(
            {
                "status": 201,
                "message": "Admin created successfully",
                "data": AdminUserSerializer(admin).data,
            },
            status=status.HTTP_201_CREATED,
        )


# ==========================================
# 2. BULK UPLOAD (Existing Logic Preserved)
# ==========================================
class CSVUploadView(APIView):
    permission_classes = [IsAuthenticated, IsAdmin]

    def post(self, request):
        school = get_requested_school(request)
        if not school:
            return Response({"error": "Select a school before uploading records."}, status=400)

        if "file" not in request.FILES:
            return Response({"error": "CSV file is required"}, status=400)

        # 1. Get Active Year for Enrollment
        active_year = get_active_academic_year(request)
        if not active_year:
            return Response({"error": "No Active Academic Year found. Set one before uploading."}, status=400)

        csv_file = request.FILES["file"]
        try:
            data = csv_file.read().decode("utf-8-sig")
        except UnicodeDecodeError:
            csv_file.seek(0)
            data = csv_file.read().decode("latin-1")

        csv_reader = csv.DictReader(io.StringIO(data))
        if not csv_reader.fieldnames:
            return Response({"error": "CSV file is empty or missing a header row."}, status=400)

        normalized_headers = {normalize_csv_key(header) for header in csv_reader.fieldnames}
        supported_student_headers = {
            "student_id", "student_name", "student_email", "father_name", "mother_name",
            "father_phone", "mother_phone", "date_of_birth", "gender", "class",
            "class_name", "standard", "section", "address", "accommodation", "accomodation",
            "date_of_admission",
        }
        if "student_id" in normalized_headers and not normalized_headers.intersection(supported_student_headers - {"student_id"}):
            return Response({
                "error": "CSV headers are not recognized.",
                "sample_format": "student_id,student_name,student_email,father_name,mother_name,father_phone,mother_phone,date_of_birth,gender,class,section,address,accommodation"
            }, status=400)

        student_count = enrollment_count = teacher_count = staff_count = 0
        updated_student_count = 0
        errors = []

        for row_number, row in enumerate(csv_reader, start=2):
            try:
                row = normalize_csv_row(row)
                # --- 1. STUDENTS ---
                if csv_value(row, "student_id"):
                    sid = csv_value(row, "student_id")
                    existing_student = Student.objects.filter(school=school, student_id=sid).first()

                    required_fields = {
                        "student_name": csv_value(row, "student_name", "name"),
                        "father_name": csv_value(row, "father_name"),
                        "mother_name": csv_value(row, "mother_name"),
                        "father_phone": csv_value(row, "father_phone"),
                        "mother_phone": csv_value(row, "mother_phone"),
                        "date_of_birth": csv_value(row, "date_of_birth", "dob"),
                        "gender": csv_value(row, "gender"),
                    }
                    missing = [field for field, value in required_fields.items() if not value]
                    if missing:
                        raise ValueError(f"Missing required fields: {', '.join(missing)}")

                    gender = required_fields["gender"].title()
                    if gender.lower() not in {"male", "female", "other"}:
                        raise ValueError("Gender must be Male, Female, or Other.")
                    
                    # A. Create Student Profile (Static)
                    student_obj, created = Student.objects.update_or_create(
                        school=school,
                        student_id=sid,
                        defaults={
                            "student_name": required_fields["student_name"],
                            "student_email": csv_value(row, "student_email", "email") or None,
                            "father_name": required_fields["father_name"],
                            "mother_name": required_fields["mother_name"],
                            "father_phone": required_fields["father_phone"],
                            "mother_phone": required_fields["mother_phone"],
                            "date_of_birth": parse_date(required_fields["date_of_birth"]),
                            "gender": gender,
                            "date_of_admission": parse_date(csv_value(row, "date_of_admission")) if csv_value(row, "date_of_admission") else timezone.now().date(),
                            "address": csv_value(row, "address") or "Not Provided",
                            "accommodation": normalize_accommodation(csv_accommodation_value(row)),
                        }
                    )
                    if created:
                        student_count += 1
                    else:
                        updated_student_count += 1

                    # B. Logic: Enroll ONLY if 'class' is present (Matches Manual API)
                    class_val = csv_value(row, "class", "class_name", "standard")
                    if class_val:
                        # Resolve Standard
                        standard_obj, _ = Standard.objects.get_or_create(
                            school=school,
                            name=str(class_val).strip(),
                        )
                        
                        # Resolve Section (Keep NULL if empty)
                        section_val = csv_value(row, "section")
                        section_obj = None
                        if section_val:
                            section_obj, _ = Section.objects.get_or_create(
                                standard=standard_obj, 
                                name=str(section_val).strip(),
                                defaults={"school": school}
                            )
                            if section_obj.school_id and section_obj.school_id != school.id:
                                raise ValueError(
                                    f"Section '{section_obj.name}' for Class '{standard_obj.name}' belongs to another school."
                                )
                            if not section_obj.school_id:
                                section_obj.school = school
                                section_obj.save(update_fields=["school"])

                        # SYNC: Update the Profile cached fields (Matches Serializer logic)
                        student_obj.standard = standard_obj
                        student_obj.section = section_obj
                        student_obj.save(update_fields=["standard", "section"])

                        # Create/Update Enrollment for Active Year
                        Enrollment.objects.update_or_create(
                            student=student_obj,
                            academic_year=active_year,
                            defaults={
                                'school': school,
                                'standard': standard_obj,
                                'section': section_obj, # Stores NULL if empty
                                'is_active': True
                            }
                        )
                        enrollment_count += 1

                # --- 2. TEACHERS ---
                if csv_value(row, "teacher_id"):
                    tid = csv_value(row, "teacher_id")
                    teacher_name = csv_value(row, "teacher_name", "name")
                    teacher_phone = csv_value(row, "teacher_phone", "phone")
                    teacher_email = csv_value(row, "teacher_email", "email")
                    teacher_dob = csv_value(row, "teacher_dob", "date_of_birth", "dob")
                    if not teacher_name or not teacher_phone or not teacher_email or not teacher_dob:
                        missing = [
                            label for label, value in (
                                ("name", teacher_name),
                                ("phone", teacher_phone),
                                ("email", teacher_email),
                                ("date of birth", teacher_dob),
                            ) if not value
                        ]
                        raise ValueError(f"Missing required teacher fields: {', '.join(missing)}")

                    Teacher.objects.update_or_create(
                        school=school,
                        teacher_id=tid,
                        defaults={
                            "name": teacher_name,
                            "phone": teacher_phone,
                            "email": teacher_email,
                            "date_of_birth": parse_date(teacher_dob),
                            "joining_date": parse_date(csv_value(row, "joining_date")) if csv_value(row, "joining_date") else None,
                            "qualification": csv_value(row, "qualification"),
                            "department": csv_value(row, "department"),
                            "address": csv_value(row, "address") or "Not Provided",
                        }
                    )
                    teacher_count += 1

                # --- 3. STAFF ---
                if csv_value(row, "staff_id"):
                    stid = csv_value(row, "staff_id")
                    _, created = NonTeachingStaff.objects.update_or_create(
                        school=school,
                        staff_id=stid,
                        defaults={
                            "name": csv_value(row, "staff_name"),
                            "phone": csv_value(row, "staff_phone"),
                            "role": csv_value(row, "staff_role"),
                        }
                    )
                    if created: staff_count += 1

            except Exception as e:
                errors.append(f"Row {row_number}: {str(e)}")

        processed_count = student_count + updated_student_count + enrollment_count + teacher_count + staff_count
        response_status = 400 if errors and processed_count == 0 else 200

        return Response({
            "message": (
                f"Bulk upload processed for {active_year.name}"
                if errors else f"Bulk Upload Success for {active_year.name}"
            ),
            "summary": {
                "profiles_created": student_count,
                "profiles_updated": updated_student_count,
                "enrolled_in_year": enrollment_count,
                "teachers": teacher_count,
                "staff": staff_count
            },
            "errors": errors
        }, status=response_status)

# ==========================================
# 3. MANUAL MANAGEMENT (CRUD Views)
# ==========================================
class AdminStudentListCreateView(generics.ListCreateAPIView):
    serializer_class = StudentProfileSerializer
    permission_classes = [IsAuthenticated, IsAdmin]

    def get_queryset(self):
        return scope_queryset_for_user(
            Student.objects.select_related("standard", "section"),
            self.request,
        ).order_by("student_id")

    def perform_create(self, serializer):
        school = get_requested_school(self.request)
        if not school:
            raise ValidationError({"school_id": "Select a school before creating students."})
        serializer.save(school=school)


class AdminStudentPagination(PageNumberPagination):
    page_size = 10
    page_size_query_param = "page_size"
    max_page_size = 100


class AdminStudentPaginatedListView(generics.ListAPIView):
    serializer_class = StudentProfileSerializer
    permission_classes = [IsAuthenticated, IsAdmin]
    pagination_class = AdminStudentPagination

    def _resolve_target_academic_year(self):
        academic_year_param = (self.request.query_params.get("academic_year") or "").strip()
        if not academic_year_param or academic_year_param.lower() == "all":
            return None, False

        if academic_year_param.lower() in {"current", "active"}:
            return get_active_academic_year(self.request), True

        if academic_year_param.isdigit():
            return scope_queryset_for_user(AcademicYear.objects.all(), self.request).filter(pk=int(academic_year_param)).first(), True

        return scope_queryset_for_user(AcademicYear.objects.all(), self.request).filter(name=academic_year_param).first(), True

    def get_queryset(self):
        queryset = scope_queryset_for_user(
            Student.objects.select_related("standard", "section"),
            self.request,
        ).order_by("student_id")

        search = (self.request.query_params.get("search") or "").strip()
        gender = (self.request.query_params.get("gender") or "").strip()
        class_name = (self.request.query_params.get("class_name") or "").strip()
        section = (self.request.query_params.get("section") or "").strip()
        assignment_status = (self.request.query_params.get("assignment_status") or "").strip().lower()
        target_year, year_requested = self._resolve_target_academic_year()
        self.target_academic_year = target_year

        if year_requested and not target_year:
            return queryset.none()

        if target_year:
            # Keep the full student roster for "All Students". Enrollment is
            # optional, so filtering the base queryset by year here silently
            # removed profiles that have not been assigned to a class yet.
            queryset = queryset.prefetch_related(
                Prefetch(
                    "enrollments",
                    queryset=Enrollment.objects.filter(academic_year=target_year).select_related("standard", "section"),
                    to_attr="enrollments_for_year",
                )
            )

        if search:
            search_q = (
                Q(student_name__icontains=search)
                | Q(student_id__icontains=search)
                | Q(student_email__icontains=search)
                | Q(address__icontains=search)
                | Q(father_phone__icontains=search)
                | Q(mother_phone__icontains=search)
            )
            if target_year:
                search_q = search_q | Q(
                    enrollments__academic_year=target_year,
                    enrollments__standard__name__icontains=search,
                ) | Q(
                    enrollments__academic_year=target_year,
                    enrollments__section__name__icontains=search,
                )
            else:
                search_q = search_q | Q(standard__name__icontains=search) | Q(section__name__icontains=search)
            queryset = queryset.filter(search_q)

        if gender and gender.lower() != "all":
            queryset = queryset.filter(gender__iexact=gender)

        if class_name and class_name.lower() != "all":
            if target_year:
                queryset = queryset.filter(
                    enrollments__academic_year=target_year,
                    enrollments__standard__name=class_name,
                )
            else:
                queryset = queryset.filter(standard__name=class_name)

        if section and section.lower() != "all":
            if target_year:
                queryset = queryset.filter(
                    enrollments__academic_year=target_year,
                    enrollments__section__name=section,
                )
            else:
                queryset = queryset.filter(section__name=section)

        if assignment_status and assignment_status != "all":
            if target_year:
                year_assigned_q = Q(
                    enrollments__academic_year=target_year,
                    enrollments__standard__isnull=False,
                    enrollments__section__isnull=False,
                )
                if assignment_status in {"assigned", "class_assigned"}:
                    queryset = queryset.filter(year_assigned_q)
                elif assignment_status in {"unassigned", "not_class_assigned"}:
                    queryset = queryset.exclude(year_assigned_q)
            else:
                if assignment_status in {"assigned", "class_assigned"}:
                    queryset = queryset.filter(standard__isnull=False, section__isnull=False)
                elif assignment_status in {"unassigned", "not_class_assigned"}:
                    queryset = queryset.filter(Q(standard__isnull=True) | Q(section__isnull=True))

        return queryset.distinct()

    def get_serializer_context(self):
        context = super().get_serializer_context()
        if getattr(self, "target_academic_year", None) is not None:
            context["academic_year"] = self.target_academic_year
        return context


class AdminStudentDetailView(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = StudentProfileSerializer
    permission_classes = [IsAuthenticated, IsAdmin]
    lookup_field = 'student_id'

    def get_queryset(self):
        return scope_queryset_for_user(Student.objects.all(), self.request)

class AdminTeacherListCreateView(generics.ListCreateAPIView):
    serializer_class = TeacherProfileSerializer
    permission_classes = [IsAuthenticated, IsAdmin]

    def get_queryset(self):
        return scope_queryset_for_user(Teacher.objects.all(), self.request).order_by("teacher_id")

    def perform_create(self, serializer):
        serializer.save(school=get_requested_school(self.request))

    def get_permissions(self):
        # Allow admin_staff to read teacher list (used by staff academics/classes page).
        if self.request.method == 'GET':
            return [IsAuthenticated(), IsOwnerAdminOrAdminStaff()]
        # Keep teacher creation restricted to owner admin login.
        return [IsAuthenticated(), IsAdmin()]


class AdminTeacherPagination(PageNumberPagination):
    page_size = 10
    page_size_query_param = "page_size"
    max_page_size = 100


class AdminTeacherPaginatedListView(generics.ListAPIView):
    serializer_class = TeacherProfileSerializer
    permission_classes = [IsAuthenticated, IsAdmin, IsOwnerAdminOrFinanceAdminStaff]
    pagination_class = AdminTeacherPagination

    def get_queryset(self):
        queryset = scope_queryset_for_user(Teacher.objects.all(), self.request).order_by("teacher_id")

        search = (self.request.query_params.get("search") or "").strip()
        department = (self.request.query_params.get("department") or "").strip()
        status_filter = (self.request.query_params.get("status") or "").strip().lower()
        class_name = (self.request.query_params.get("class_name") or "").strip()
        section = (self.request.query_params.get("section") or "").strip()

        active_year = AcademicYear.objects.filter(is_current=True).first()

        if search:
            search_q = (
                Q(name__icontains=search)
                | Q(teacher_id__icontains=search)
                | Q(email__icontains=search)
                | Q(phone__icontains=search)
                | Q(department__icontains=search)
                | Q(qualification__icontains=search)
                | Q(address__icontains=search)
            )
            if active_year:
                search_q = search_q | Q(
                    class_teacher_of__academic_year=active_year,
                    class_teacher_of__section__standard__name__icontains=search,
                ) | Q(
                    class_teacher_of__academic_year=active_year,
                    class_teacher_of__section__name__icontains=search,
                )
            queryset = queryset.filter(search_q)

        if department and department.lower() != "all":
            queryset = queryset.filter(department=department)

        if status_filter == "assigned":
            if active_year:
                queryset = queryset.filter(class_teacher_of__academic_year=active_year)
            else:
                queryset = queryset.none()
        elif status_filter == "unassigned":
            if active_year:
                queryset = queryset.exclude(class_teacher_of__academic_year=active_year)

        if class_name and class_name.lower() != "all":
            if active_year:
                queryset = queryset.filter(
                    class_teacher_of__academic_year=active_year,
                    class_teacher_of__section__standard__name=class_name,
                )
            else:
                queryset = queryset.none()

        if section and section.lower() != "all":
            if active_year:
                queryset = queryset.filter(
                    class_teacher_of__academic_year=active_year,
                    class_teacher_of__section__name=section,
                )
            else:
                queryset = queryset.none()

        return queryset.distinct()


class AdminTeacherDetailView(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = TeacherProfileSerializer
    permission_classes = [IsAuthenticated, IsAdmin]
    lookup_field = 'teacher_id'

    def get_queryset(self):
        return scope_queryset_for_user(Teacher.objects.all(), self.request)

    def perform_destroy(self, instance):
        # A Teacher owns its link to User, so deleting the profile alone leaves
        # the login row (and its globally unique username) behind.
        linked_user = instance.user
        with transaction.atomic():
            instance.delete()
            if linked_user is not None:
                linked_user.delete()

class AdminStaffListCreateView(generics.ListCreateAPIView):
    serializer_class = StaffProfileSerializer
    permission_classes = [IsAuthenticated, IsAdmin, IsOwnerAdminOrFinanceAdminStaff]

    def get_queryset(self):
        return scope_queryset_for_user(NonTeachingStaff.objects.all(), self.request).order_by("staff_id")

    def perform_create(self, serializer):
        serializer.save(school=get_requested_school(self.request))


class AdminStaffPagination(PageNumberPagination):
    page_size = 10
    page_size_query_param = "page_size"
    max_page_size = 100


class AdminStaffPaginatedListView(generics.ListAPIView):
    serializer_class = StaffProfileSerializer
    permission_classes = [IsAuthenticated, IsOwnerAdminOrAdminStaffOrHostelWarden]
    pagination_class = AdminStaffPagination

    def get_queryset(self):
        queryset = scope_queryset_for_user(NonTeachingStaff.objects.all(), self.request).order_by("staff_id")

        search = (self.request.query_params.get("search") or "").strip()
        role = (self.request.query_params.get("role") or "").strip()

        if search:
            queryset = queryset.filter(
                Q(name__icontains=search)
                | Q(staff_id__icontains=search)
                | Q(role__icontains=search)
                | Q(email__icontains=search)
                | Q(phone__icontains=search)
                | Q(address__icontains=search)
            )

        if role and role.lower() != "all":
            queryset = queryset.filter(role=role)

        return queryset


class AdminStaffDetailView(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = StaffProfileSerializer
    permission_classes = [IsAuthenticated, IsAdmin]
    lookup_field = 'staff_id'

    def get_queryset(self):
        return scope_queryset_for_user(NonTeachingStaff.objects.all(), self.request)


# ==========================================
# 4. SETUP UTILS (Assign Teachers) - REFACTORED
# ==========================================
class AssignClassTeacherView(APIView):
    """
    Manually assigns a Teacher to a specific Class and Section
    FOR THE ACTIVE ACADEMIC YEAR.
    """
    permission_classes = [IsAuthenticated, IsOwnerAdminOrAdminStaff]

    def post(self, request):
        data = request.data
        school = get_requested_school(request)
        if not school:
            return Response({"error": "Select a school before assigning class teachers."}, status=400)
        
        # 0. Get Active Year
        try:
            active_year = AcademicYear.objects.get(is_current=True)
        except AcademicYear.DoesNotExist:
            return Response({"error": "No Active Academic Year Configured"}, status=500)

        # 1. Get Inputs
        teacher_id = data.get("teacher_id") 
        class_name = data.get("class_name") 
        section_name = data.get("section") 

        if not all([teacher_id, class_name, section_name]):
            return Response({"error": "Teacher ID, Class, and Section are required"}, status=400)

        try:
            # 2. Find the Teacher
            teacher = scope_queryset_for_user(Teacher.objects.all(), request).get(teacher_id=teacher_id)

            # 3. Find the Section
            section = Section.objects.get(
                name=section_name, 
                standard__name=class_name,
                school=school,
            )

            # 4. Assign Class Teacher (Update or Create logic)
            # Logic: A section can have only ONE ClassTeacher per YEAR.
            # This will update the teacher if a record already exists for this Section+Year.
            class_teacher_record, created = ClassTeacher.objects.update_or_create(
                section=section,
                academic_year=active_year,
                defaults={'teacher': teacher, 'school': school}
            )

            action = "assigned" if created else "updated"

            return Response({
                "message": f"Class Teacher {action} successfully for {active_year.name}",
                "teacher": teacher.name,
                "class": f"{class_name} - {section_name}"
            }, status=200)

        except Teacher.DoesNotExist:
            return Response({"error": f"Teacher with ID {teacher_id} not found"}, status=404)
        except Section.DoesNotExist:
            return Response({"error": f"Class {class_name} Section {section_name} not found"}, status=404)
        except Exception as e:
            return Response({"error": str(e)}, status=400)


from django.utils import timezone
from school.models import Institution, School


def _absolute_file_url(request, file_field):
    if not file_field:
        return None
    try:
        return request.build_absolute_uri(file_field.url)
    except Exception:
        return None

# Keeping AdminDashboardView changed as per academic year
class AdminDashboardView(APIView):
    permission_classes = [IsAuthenticated, IsAdmin]

    def get(self, request):
        school = get_requested_school(request)
        super_admin_view = is_super_admin(request.user)

        # 1. Active Academic Year
        active_year_query = AcademicYear.objects.filter(is_current=True)
        if school:
            active_year_query = active_year_query.filter(Q(school=school) | Q(school__isnull=True))
        active_year = active_year_query.order_by('-start_date').first()
        if not active_year:
            return Response({"error": "No Active Academic Year found"}, status=500)

        # 2. Previous Academic Year
        previous_year = (
            AcademicYear.objects
            .filter(is_current=False, start_date__lt=active_year.start_date)
            .order_by('-start_date')
            .first()
        )

        # 3. Student counts
        total_students_now = Enrollment.objects.filter(
            academic_year=active_year,
            is_active=True
        )
        if school:
            total_students_now = total_students_now.filter(school=school)
        total_students_now = total_students_now.count()

        students_prev_year = 0
        if previous_year:
            students_prev_year = Enrollment.objects.filter(
                academic_year=previous_year,
                is_active=True
            )
            if school:
                students_prev_year = students_prev_year.filter(school=school)
            students_prev_year = students_prev_year.count()

        # 4. Difference & percentage
        diff = total_students_now - students_prev_year

        if students_prev_year == 0:
            percentage = 100 if total_students_now > 0 else 0
        else:
            percentage = (diff / students_prev_year) * 100

        # 5. Trend
        if diff > 0:
            trend = "increase"
        elif diff < 0:
            trend = "decrease"
        else:
            trend = "stable"

        # 6. School name
        school_name = "All Schools" if getattr(request.user, "user_type", "") == "super_admin" else "School Name Not Set"
        if school:
            school_name = school.name

        institution = school.institution if school and school.institution else Institution.objects.first()
        logo_url = _absolute_file_url(request, school.logo) if school else None
        institution_logo_url = _absolute_file_url(request, institution.logo) if institution else None

        schools_queryset = School.objects.all()
        if school:
            schools_queryset = schools_queryset.filter(pk=school.pk)
        active_schools = schools_queryset.filter(is_active=True).count()
        total_schools = schools_queryset.count()
        total_teachers = scope_queryset_for_user(Teacher.objects.all(), request).count()
        total_staff = scope_queryset_for_user(NonTeachingStaff.objects.all(), request).count()

        # grammar handling
        word = "student" if abs(diff) == 1 else "students"

        return Response({
            "role_scope": "institution" if super_admin_view and not school else "school",
            "school_name": school_name,
            "school_id": school.id if school else None,
            "school_logo": logo_url,
            "institution_name": (
                school.institution.name
                if school and school.institution
                else institution.name if institution else "Institution Overview"
            ),
            "institution_logo": institution_logo_url,
            "total_schools": total_schools,
            "active_schools": active_schools,
            "active_academic_year": active_year.name,
            "total_students": total_students_now,
            "total_teachers": total_teachers,
            "total_staff": total_staff,
            "trend": trend,

            # ⭐ management friendly
            "change_in_students": diff,

            # ⭐ small indicator
            "percentage_change": f"{round(abs(percentage), 1)}%",

            "comparison_text": f"{'+' if diff > 0 else ''}{diff} {word} vs last year"
        })


# ==========================================
# 5. VIEW ALLOCATIONS - REFACTORED
# ==========================================
class TeachersByClassView(APIView):
    """
    Fetch teachers handling a specific Class (Standard) IN THE ACTIVE YEAR.
    
    Query Params: ?class_name=10
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        # 0. Get Active Year
        try:
            active_year = AcademicYear.objects.get(is_current=True)
        except AcademicYear.DoesNotExist:
            return Response({"error": "No Active Academic Year Configured"}, status=500)

        class_name = request.query_params.get('class_name')
        subject_filter = request.query_params.get('subject_name')

        if not class_name:
            return Response({"error": "Please provide 'class_name' parameter"}, 400)

        # 1. Get Allocations (Filtered by Active Year & Standard)
        # Note: Allocations are now linked to 'standard', not 'allocated_classes' M2M
        allocations = TeacherAllocation.objects.filter(
            standard__name=class_name,
            academic_year=active_year
        )
        
        if subject_filter:
            allocations = allocations.filter(subject__name__iexact=subject_filter)
            
        allocations = allocations.select_related('teacher', 'subject').distinct()

        # 2. GROUPING LOGIC
        grouped_teachers = {}

        for alloc in allocations:
            t_id = alloc.teacher.teacher_id
            
            if t_id not in grouped_teachers:
                # --- NEW LOGIC: Safely grab the full Image URL ---
                profile_img_url = None
                if alloc.teacher.profile_image:
                    profile_img_url = alloc.teacher.profile_image.url

                grouped_teachers[t_id] = {
                    "teacher_name": alloc.teacher.name,
                    "teacher_id": t_id,
                    "profile_image": profile_img_url,
                    "subjects": [],
                    "class": class_name,
                    "year": active_year.name
                }
            
            subj_name = alloc.subject.name
            if subj_name not in grouped_teachers[t_id]["subjects"]:
                grouped_teachers[t_id]["subjects"].append(subj_name)

        response_data = list(grouped_teachers.values())

        return Response(response_data)


# ==========================================
# 5. CLASS TEACHER MANAGER (View & Delete)
# ==========================================
class ClassTeacherManagerView(APIView):
    permission_classes = [IsAuthenticated, IsAdmin]

    # --- GET: View Class Teachers for a Standard ---
    def get(self, request):
        class_name = request.query_params.get('class_name') # e.g. "9"
        
        if not class_name:
            return Response({"error": "Param 'class_name' is required"}, 400)

        try:
            active_year = AcademicYear.objects.get(is_current=True)
        except AcademicYear.DoesNotExist:
            return Response({"error": "No Active Year"}, 500)

        # Fetch Class Teachers for this Class & Year
        assignments = ClassTeacher.objects.filter(
            section__standard__name=class_name,
            academic_year=active_year
        ).select_related('teacher', 'section')

        data = []
        for assign in assignments:
            data.append({
                "teacher_name": assign.teacher.name,
                "teacher_id": assign.teacher.teacher_id,
                "class": assign.section.standard.name,
                "section": assign.section.name,
                "academic_year": active_year.name
            })

        return Response({
            "status": 200,
            "class": class_name,
            "count": len(data),
            "data": data
        })

    # --- DELETE: Remove Class Teacher Assignment ---
    def delete(self, request):
        class_name = request.query_params.get('class_name')
        section_name = request.query_params.get('section')

        if not all([class_name, section_name]):
            return Response({"error": "Params 'class_name' and 'section' are required"}, 400)

        try:
            active_year = AcademicYear.objects.get(is_current=True)
        except:
            return Response({"error": "No Active Year"}, 500)

        # Find and delete
        deleted_count, _ = ClassTeacher.objects.filter(
            section__standard__name=class_name,
            section__name=section_name,
            academic_year=active_year
        ).delete()

        if deleted_count > 0:
            return Response({"message": f"Class Teacher removed for {class_name}-{section_name} ({active_year.name})"})
        else:
            return Response({"error": "No Class Teacher found for this section in the active year."}, 404)


# ==========================================
# 6. SUBJECT TEACHER MANAGER (Smart Delete & Kill Switch)
# ==========================================
class SubjectTeacherManagerView(APIView):
    """
    Deletes Teacher Permissions.
    CRITICAL: Deletes BOTH TeacherAllocation (Eligibility) AND TimetableSlot (Active Access).
    This ensures the teacher immediately loses access to Views/Uploads.
    """
    permission_classes = [IsAuthenticated, IsAdmin]

    def delete(self, request):
        teacher_id = request.query_params.get('teacher_id')
        class_name = request.query_params.get('class_name')
        subject_name = request.query_params.get('subject') # Optional

        if not teacher_id or not class_name:
            return Response({"error": "Params 'teacher_id' and 'class_name' are required"}, 400)

        try:
            active_year = AcademicYear.objects.get(is_current=True)
        except:
            return Response({"error": "No Active Year"}, 500)

        # 1. Prepare Filters
        # We need to filter both Allocations (Standard-level) and Slots (Section-level)
        
        # Filter for Allocations
        alloc_filter = {
            "teacher__teacher_id": teacher_id,
            "standard__name": class_name,
            "academic_year": active_year
        }
        
        # Filter for Timetable Slots
        # Note: Slots link to 'section', so we filter section__standard__name
        slot_filter = {
            "teacher__teacher_id": teacher_id,
            "section__standard__name": class_name,
            "academic_year": active_year
        }

        if subject_name:
            alloc_filter["subject__name__iexact"] = subject_name
            slot_filter["subject__name__iexact"] = subject_name
            msg_type = f"Subject '{subject_name}'"
        else:
            msg_type = "All Subject Permissions"

        # 2. EXECUTE KILL SWITCH (Delete Slots First)
        # This immediately revokes access to Upload Marks, Reports, etc.
        deleted_slots, _ = TimetableSlot.objects.filter(**slot_filter).delete()

        # 3. Delete Allocations
        # This cleans up the "Eligibility" records
        deleted_allocs, _ = TeacherAllocation.objects.filter(**alloc_filter).delete()

        if deleted_allocs > 0 or deleted_slots > 0:
            return Response({
                "status": 200,
                "message": f"Revoked {msg_type} for Teacher {teacher_id} in Class {class_name}",
                "details": {
                    "allocation_records_removed": deleted_allocs,
                    "timetable_slots_removed": deleted_slots,
                    "access_status": "REVOKED IMMEDIATE"
                }
            })
        else:
            return Response({"error": "No matching permissions or slots found to delete."}, 404)


##goutham new dashboard # # # # #

class AdminDashboardStatsView(APIView):
    permission_classes = [IsAuthenticated, IsAdmin]

    def get(self, request):
        today = timezone.now().date()
        school = get_requested_school(request)
        teacher_queryset = scope_queryset_for_user(Teacher.objects.all(), request)
        staff_queryset = scope_queryset_for_user(NonTeachingStaff.objects.all(), request)
        student_queryset = scope_queryset_for_user(Student.objects.all(), request)
        enrollment_queryset = scope_queryset_for_user(Enrollment.objects.all(), request)
        standard_queryset = scope_queryset_for_user(Standard.objects.all(), request)
        section_queryset = scope_queryset_for_user(Section.objects.all(), request)

        # --- TEACHER STATS ---
        total_teachers = teacher_queryset.count()
        
        # Count as present if status is 'Present' or 'Late'
        teachers_present_today = TeacherAttendance.objects.filter(
            date=today, 
            status__in=['Present', 'Late'],
            teacher__in=teacher_queryset,
        ).count()
        
        pending_teacher_leaves = LeaveRequest.objects.filter(
            user_type='teacher', 
            status='Pending',
            teacher__in=teacher_queryset,
        ).count()

        # --- NON-TEACHING STAFF STATS ---
        total_staff = staff_queryset.count()
        
        staff_present_today = StaffAttendance.objects.filter(
            date=today, 
            status__in=['Present', 'Late'],
            staff__in=staff_queryset,
        ).count()
        
        pending_staff_leaves = LeaveRequest.objects.filter(
            user_type='staff', 
            status='Pending',
            staff__in=staff_queryset,
        ).count()

        active_year = AcademicYear.objects.filter(is_current=True).first()
        student_attendance_queryset = Attendance.objects.filter(date=today)
        if active_year:
            student_attendance_queryset = student_attendance_queryset.filter(enrollment__academic_year=active_year)
        student_attendance_queryset = student_attendance_queryset.filter(enrollment__in=enrollment_queryset)

        student_total = student_queryset.count()
        student_present = student_attendance_queryset.filter(status__iexact='present').count()
        student_absent = student_attendance_queryset.filter(status__iexact='absent').count()
        student_late = student_attendance_queryset.filter(status__iexact='late').count()
        total_marked = student_present + student_absent + student_late
        attendance_percentage = round(((student_present + student_late) / total_marked) * 100, 1) if total_marked else 0

        gender_counts = student_queryset.aggregate(
            male=Count('id', filter=Q(gender__iexact='male')),
            female=Count('id', filter=Q(gender__iexact='female')),
            other=Count('id', filter=~Q(gender__iexact='male') & ~Q(gender__iexact='female')),
        )

        school_queryset = School.objects.all()
        if school:
            school_queryset = school_queryset.filter(pk=school.pk)

        school_breakdown = []
        if is_super_admin(request.user) and not school:
            for school_obj in School.objects.annotate(
                students_total=Count('students', distinct=True),
                teachers_total=Count('teachers', distinct=True),
                staff_total=Count('staff_members', distinct=True),
            ).order_by('name')[:12]:
                school_breakdown.append({
                    "id": school_obj.id,
                    "name": school_obj.name,
                    "code": school_obj.code,
                    "logo": _absolute_file_url(request, school_obj.logo),
                    "institution_name": school_obj.institution.name if school_obj.institution else None,
                    "students": school_obj.students_total,
                    "teachers": school_obj.teachers_total,
                    "staff": school_obj.staff_total,
                    "is_active": school_obj.is_active,
                })

        # --- FINAL DATA STRUCTURE ---
        data = {
            "meta": {
                "date": str(today),
                "academic_year": active_year.name if active_year else "Not Set",
                "generated_at": timezone.now().isoformat(),
                "role_scope": "institution" if is_super_admin(request.user) and not school else "school",
                "school_id": school.id if school else None,
                "school_name": school.name if school else "All Schools",
                "school_logo": _absolute_file_url(request, school.logo) if school else None,
                "institution_name": (
                    school.institution.name
                    if school and school.institution
                    else Institution.objects.first().name if Institution.objects.exists() else "Institution Overview"
                ),
                "institution_logo": (
                    _absolute_file_url(request, school.institution.logo)
                    if school and school.institution
                    else _absolute_file_url(request, Institution.objects.first().logo) if Institution.objects.exists() else None
                ),
                "time_periods": {
                    "daily": today.strftime("%d %b %Y"),
                    "weekly": "This Week",
                    "monthly": today.strftime("%B %Y"),
                },
                "schools": {
                    "total": school_queryset.count(),
                    "active": school_queryset.filter(is_active=True).count(),
                },
                "school_breakdown": school_breakdown,
            },
            "students": {
                "total": student_total,
                "gender_distribution": {
                    "male": gender_counts["male"] or 0,
                    "female": gender_counts["female"] or 0,
                    "other": gender_counts["other"] or 0,
                    "male_percentage": round(((gender_counts["male"] or 0) / student_total) * 100, 1) if student_total else 0,
                    "female_percentage": round(((gender_counts["female"] or 0) / student_total) * 100, 1) if student_total else 0,
                },
                "today": {
                    "present": student_present,
                    "absent": student_absent,
                    "late": student_late,
                    "total_marked": total_marked,
                    "unmarked": max(student_total - total_marked, 0),
                    "attendance_percentage": attendance_percentage,
                },
                "overall": {
                    "attendance_percentage": attendance_percentage,
                    "present_this_month": student_present,
                    "absent_this_month": student_absent,
                    "late_this_month": student_late,
                    "monthly_summary": {
                        "present_percentage": attendance_percentage,
                        "absent_percentage": round((student_absent / total_marked) * 100, 1) if total_marked else 0,
                        "late_percentage": round((student_late / total_marked) * 100, 1) if total_marked else 0,
                    },
                },
            },
            "teachers": {
                "total": total_teachers,
                "present_today": teachers_present_today,
                "pending_leaves": pending_teacher_leaves,
                "today": {
                    "present": teachers_present_today,
                    "late": TeacherAttendance.objects.filter(date=today, status='Late', teacher__in=teacher_queryset).count(),
                    "absent": TeacherAttendance.objects.filter(date=today, status='Absent', teacher__in=teacher_queryset).count(),
                    "attendance_percentage": round((teachers_present_today / total_teachers) * 100, 1) if total_teachers else 0,
                },
            },
            "non_teaching_staff": {
                "total": total_staff,
                "present_today": staff_present_today,
                "pending_leaves": pending_staff_leaves
            },
            "staff": {
                "total": total_staff,
                "present_today": staff_present_today,
                "pending_leaves": pending_staff_leaves,
                "today": {
                    "present": staff_present_today,
                    "late": StaffAttendance.objects.filter(date=today, status='Late', staff__in=staff_queryset).count(),
                    "absent": StaffAttendance.objects.filter(date=today, status='Absent', staff__in=staff_queryset).count(),
                    "attendance_percentage": round((staff_present_today / total_staff) * 100, 1) if total_staff else 0,
                },
            },
            "academics": {
                "total_classes": standard_queryset.count(),
                "total_sections": section_queryset.count(),
                "class_details": [],
                "system_defined": {
                    "total_classes": standard_queryset.count(),
                    "total_sections": section_queryset.count(),
                },
                "inactive": {
                    "classes": 0,
                    "sections": 0,
                },
            },
        }

        return Response(data, status=200)


# transport dashboard stats view
class TransportDashboardStatsView(APIView):
    permission_classes = [IsAuthenticated, IsAdmin]

    def get(self, request):
        today = timezone.now().date()

        # 1. FLEET METRICS
        total_buses = Vehicle.objects.count()
        buses_with_drivers = Vehicle.objects.filter(driver__isnull=False).count()
        active_routes_now = Route.objects.filter(is_active=True).count()

        # 2. CAPACITY & UTILIZATION
        # Get total capacity of the entire fleet
        fleet_capacity = Vehicle.objects.aggregate(total_seats=Sum('capacity'))['total_seats'] or 0
        
        # Get total people allocated
        total_passengers = TransportAllocation.objects.count()
        
        # Calculate Percentage (Avoid division by zero)
        utilization_rate = 0
        if fleet_capacity > 0:
            utilization_rate = round((total_passengers / fleet_capacity) * 100, 1)

        # 3. ACTIONABLE ALERTS (Near Full)
        # Find buses with less than 10% seats remaining
        buses = Vehicle.objects.annotate(occupied=Count('transportallocation'))
        near_full_count = 0
        
        for bus in buses:
            if bus.capacity > 0:
                if (bus.occupied / bus.capacity) >= 0.9: # 90% Full
                    near_full_count += 1

        # 4. EXPENSES
        expenses_today = TransportExpenseProof.objects.filter(timestamp__date=today).count()

        data = {
            "fleet_status": {
                "total_buses": total_buses,
                "active_on_road": active_routes_now,
                "drivers_assigned": buses_with_drivers
            },
            "occupancy_health": {
                "total_seats": fleet_capacity,
                "allocated_seats": total_passengers,
                "utilization_percentage": f"{utilization_rate}%",
                "buses_near_full": near_full_count
            },
            "pending_actions": {
                "new_expense_proofs": expenses_today,
                "unassigned_buses": total_buses - buses_with_drivers
            }
        }

        return Response(data, status=200)


class BulkProfileImageZipUploadView(APIView):
    """
    POST: Bulk upload profile images via a single .zip file.
    Payload should include:
    - type: 'student', 'teacher', or 'staff'
    - zip_file: [A single .zip file containing the images]
    """
    permission_classes = [IsAuthenticated, IsAdmin]
    parser_classes = (MultiPartParser, FormParser)

    def post(self, request):
        user_type = request.data.get('type')
        zip_file = request.FILES.get('zip_file')
        school = get_requested_school(request)

        if not school:
            return Response({"error": "Select a school before uploading profile images."}, status=400)

        if not user_type or user_type not in ['student', 'teacher', 'staff']:
            return Response({"error": "Please provide a valid type ('student', 'teacher', 'staff')."}, status=400)
            
        if not zip_file or not zip_file.name.endswith('.zip'):
            return Response({"error": "Please upload a valid .zip file."}, status=400)

        success_count = 0
        errors = []

        try:
            # 1. Open the uploaded zip file
            with zipfile.ZipFile(zip_file, 'r') as archive:
                for file_path in archive.namelist():
                    
                    # Skip directories and hidden Mac files (like __MACOSX or .DS_Store)
                    if file_path.endswith('/') or '__MACOSX' in file_path or file_path.split('/')[-1].startswith('.'):
                        continue

                    # 2. Extract just the filename from the path inside the zip
                    file_name_with_ext = os.path.basename(file_path)
                    identifier, extension = os.path.splitext(file_name_with_ext)
                    identifier = identifier.strip()

                    # Check if it's an image file
                    if extension.lower() not in ['.jpg', '.jpeg', '.png']:
                        errors.append(f"Skipped {file_name_with_ext}: Not a valid image format.")
                        continue

                    # 3. Read the actual image data from the zip
                    image_data = archive.read(file_path)

                    # 4. Find the correct user and save the image
                    try:
                        if user_type == 'student':
                            person = Student.objects.get(school=school, student_id=identifier)
                        elif user_type == 'teacher':
                            person = Teacher.objects.get(school=school, teacher_id=identifier)
                        elif user_type == 'staff':
                            person = NonTeachingStaff.objects.get(school=school, staff_id=identifier)

                        # Wrap the raw data in a Django ContentFile and save it
                        person.profile_image.save(file_name_with_ext, ContentFile(image_data), save=True)
                        success_count += 1

                    except (Student.DoesNotExist, Teacher.DoesNotExist, NonTeachingStaff.DoesNotExist):
                        errors.append(f"No {user_type} found with ID: '{identifier}' (File: {file_name_with_ext})")
                    except Exception as e:
                        errors.append(f"Error saving {file_name_with_ext}: {str(e)}")

        except zipfile.BadZipFile:
            return Response({"error": "The uploaded file is not a valid zip archive."}, status=400)

        return Response({
            "message": "Bulk ZIP upload processed.",
            "success_count": success_count,
            "failed_count": len(errors),
            "errors": errors
        }, status=status.HTTP_200_OK)


### SIVA BRO CODE###
from datetime import date, timedelta, datetime
from django.utils import timezone
from django.db.models import Count, Q
from collections import defaultdict

class RecentActivitiesView(APIView):
    """
    API to fetch recent activities from the last 7 days.
    Returns simple messages showing "what happened" and "who did it".
    """
    permission_classes = [IsAuthenticated, IsAdmin]

    def get(self, request):
        today = timezone.now().date()
        seven_days_ago = today - timedelta(days=7)
        
        activities = []
        
        # ==========================================
        # 1. STAFF WORK ACTIVITIES
        # ==========================================
        try:
            from staff_work.models import WorkAssignment, WorkOrder
            
            # New tasks assigned
            new_assignments = WorkAssignment.objects.filter(
                work_order_created_date_range=[seven_days_ago, today]
            ).select_related('staff', 'work_order')
            
            for assign in new_assignments:
                activities.append({
                    "message": f"Task assigned: {assign.work_order.description[:40]}",
                    "who": assign.staff.name,
                    "timestamp": assign.work_order.created_date,
                    "category": "staff_work"
                })
            
            # Tasks completed
            completed_tasks = WorkAssignment.objects.filter(
                completed_at_date_range=[seven_days_ago, today],
                status='Completed'
            ).select_related('staff', 'work_order')
            
            for task in completed_tasks:
                activities.append({
                    "message": f"Task completed: {task.work_order.description[:40]}",
                    "who": task.staff.name,
                    "timestamp": task.completed_at.date(),
                    "category": "staff_work"
                })
        except Exception as e:
            print(f"Staff work error: {e}")

        # ==========================================
        # 2. TEACHER ATTENDANCE
        # ==========================================
        try:
            from attendance.models import TeacherAttendance
            
            teacher_attendance = TeacherAttendance.objects.filter(
                date__range=[seven_days_ago, today]
            ).select_related('teacher')
            
            for att in teacher_attendance:
                activities.append({
                    "message": f"{att.status} attendance marked",
                    "who": att.teacher.name,
                    "timestamp": att.date,
                    "category": "teacher_attendance"
                })
        except Exception as e:
            print(f"Teacher attendance error: {e}")

        # ==========================================
        # 3. STAFF ATTENDANCE
        # ==========================================
        try:
            from attendance.models import StaffAttendance
            
            staff_attendance = StaffAttendance.objects.filter(
                date__range=[seven_days_ago, today]
            ).select_related('staff')
            
            for att in staff_attendance:
                activities.append({
                    "message": f"{att.status} attendance marked",
                    "who": att.staff.name,
                    "timestamp": att.date,
                    "category": "staff_attendance"
                })
        except Exception as e:
            print(f"Staff attendance error: {e}")

        # ==========================================
        # 4. STUDENT ATTENDANCE (Class Teachers)
        # ==========================================
        try:
            from attendance.models import Attendance
            from academics.models import ClassTeacher
            from school.models import AcademicYear
            
            # Get unique dates where attendance was marked
            attendance_dates = Attendance.objects.filter(
                date__range=[seven_days_ago, today]
            ).dates('date', 'day')
            
            active_year = AcademicYear.objects.filter(is_current=True).first()
            
            for att_date in attendance_dates:
                if active_year:
                    class_teachers = ClassTeacher.objects.filter(
                        academic_year=active_year,
                        section_enrollmentattendance_records_date=att_date
                    ).distinct().select_related('teacher', 'section')
                    
                    for ct in class_teachers[:5]:  # Limit to avoid duplicates
                        count = Attendance.objects.filter(
                            date=att_date,
                            enrollment__section=ct.section
                        ).count()
                        
                        if count > 0:
                            activities.append({
                                "message": f"Attendance marked for {ct.section.standard.name}-{ct.section.name} ({count} students)",
                                "who": ct.teacher.name,
                                "timestamp": att_date,
                                "category": "student_attendance"
                            })
        except Exception as e:
            print(f"Student attendance error: {e}")

        # ==========================================
        # 5. INVENTORY ACTIVITIES
        # ==========================================
        try:
            from inventory.models import StockLog
            
            inventory_logs = StockLog.objects.filter(
                timestamp_date_range=[seven_days_ago, today]
            ).select_related('staff', 'item')
            
            for log in inventory_logs:
                who_name = log.staff.name if log.staff else "Admin"
                
                action_msg = {
                    'used': 'Used',
                    'damaged': 'Reported damaged',
                    'restocked': 'Restocked'
                }.get(log.action, log.action)
                
                activities.append({
                    "message": f"{action_msg}: {log.item.stock_name}",
                    "who": who_name,
                    "timestamp": log.timestamp.date(),
                    "category": "inventory"
                })
        except Exception as e:
            print(f"Inventory error: {e}")

        # ==========================================
        # 6. LEAVE REQUESTS
        # ==========================================
        try:
            from leave_management.models import LeaveRequest
            
            # New leave applications
            new_leaves = LeaveRequest.objects.filter(
                created_at_date_range=[seven_days_ago, today]
            ).select_related('student', 'staff', 'teacher')
            
            for leave in new_leaves:
                name = ""
                if leave.student: 
                    name = leave.student.student_name
                    role = "Student"
                elif leave.staff: 
                    name = leave.staff.name
                    role = "Staff"
                elif leave.teacher: 
                    name = leave.teacher.name
                    role = "Teacher"
                else:
                    name = "Unknown"
                    role = ""
                
                activities.append({
                    "message": f"Leave applied: {name} ({role}) - {leave.start_date} to {leave.end_date}",
                    "who": name,
                    "timestamp": leave.created_at.date(),
                    "category": "leave"
                })
            
            # Leave approvals/rejections
            processed_leaves = LeaveRequest.objects.filter(
                status__in=['Approved', 'Rejected'],
                updated_at_date_range=[seven_days_ago, today]
            ).select_related('student', 'staff', 'teacher')
            
            for leave in processed_leaves:
                name = ""
                if leave.student: name = leave.student.student_name
                elif leave.staff: name = leave.staff.name
                elif leave.teacher: name = leave.teacher.name
                else: name = "Unknown"
                
                approver = leave.approved_by_name or "Admin"
                
                activities.append({
                    "message": f"Leave {leave.status.lower()}: {name}",
                    "who": approver,
                    "timestamp": leave.updated_at.date() if hasattr(leave, 'updated_at') else leave.created_at.date(),
                    "category": "leave"
                })
        except Exception as e:
            print(f"Leave error: {e}")

        # ==========================================
        # 7. EXAM MARKS UPLOAD
        # ==========================================
        try:
            from exams.models import StudentMark
            
            # Since there's no direct tracking of who uploaded marks,
            # we'll show a summary
            marks_count = StudentMark.objects.filter(
                enrollment_academic_year_is_current=True
            ).count()
            
            recent_marks = StudentMark.objects.filter(
                enrollment_academic_year_is_current=True
            )[:1]
            
            if marks_count > 0:
                activities.append({
                    "message": f"Exam marks uploaded/updated ({marks_count} entries total)",
                    "who": "Teachers",
                    "timestamp": today,
                    "category": "exam_marks"
                })
        except Exception as e:
            print(f"Exam marks error: {e}")

        # ==========================================
        # 8. BEHAVIOR REPORTS
        # ==========================================
        try:
            from reports.models import BehaviorReport
            
            new_reports = BehaviorReport.objects.filter(
                created_at_date_range=[seven_days_ago, today]
            ).select_related('teacher', 'enrollment__student', 'subject')
            
            for report in new_reports:
                teacher_name = "Teacher"
                if hasattr(report.teacher, 'teacher_profile'):
                    teacher_name = report.teacher.teacher_profile.name
                elif hasattr(report.teacher, 'username'):
                    teacher_name = report.teacher.username
                
                student_name = report.enrollment.student.student_name if report.enrollment and report.enrollment.student else "Student"
                
                activities.append({
                    "message": f"Behavior report: {student_name} - {report.subject.name}",
                    "who": teacher_name,
                    "timestamp": report.created_at.date(),
                    "category": "behavior"
                })
        except Exception as e:
            print(f"Behavior report error: {e}")

        # ==========================================
        # 9. STUDENT CRUD (via CSV Uploads)
        # ==========================================
        try:
            from students.models import Student
            from schooladmin.views import CSVUploadView
            
            # Since Student model doesn't have created_at/updated_at,
            # we'll track via the CSV upload functionality
            # For now, we'll add a generic message
            activities.append({
                "message": "Student records management performed",
                "who": "Admin",
                "timestamp": today,
                "category": "student_management"
            })
        except Exception as e:
            print(f"Student CRUD error: {e}")

        # ==========================================
        # 10. TEACHER CRUD
        # ==========================================
        try:
            from teachers.models import Teacher
            
            # Check if Teacher model has date fields
            if hasattr(Teacher, 'created_at'):
                new_teachers = Teacher.objects.filter(
                    created_at_date_range=[seven_days_ago, today]
                )
                
                for teacher in new_teachers:
                    activities.append({
                        "message": f"New teacher added: {teacher.name}",
                        "who": "Admin",
                        "timestamp": teacher.created_at.date(),
                        "category": "teacher_management"
                    })
        except Exception as e:
            print(f"Teacher CRUD error: {e}")

        # ==========================================
        # 11. STAFF CRUD
        # ==========================================
        try:
            from staff.models import NonTeachingStaff
            
            # Check if Staff model has date fields
            if hasattr(NonTeachingStaff, 'created_at'):
                new_staff = NonTeachingStaff.objects.filter(
                    created_at_date_range=[seven_days_ago, today]
                )
                
                for staff in new_staff:
                    activities.append({
                        "message": f"New staff added: {staff.name} ({staff.role})",
                        "who": "Admin",
                        "timestamp": staff.created_at.date(),
                        "category": "staff_management"
                    })
        except Exception as e:
            print(f"Staff CRUD error: {e}")

        # ==========================================
        # 12. BULK UPLOAD - From CSVUploadView
        # ==========================================
        try:
            # Since we can't directly track CSV uploads, we'll add based on student count pattern
            activities.append({
                "message": "Bulk data upload performed (students/teachers/staff)",
                "who": "Admin",
                "timestamp": today,
                "category": "bulk_upload"
            })
        except Exception as e:
            print(f"Bulk upload error: {e}")

        # ==========================================
        # 13. CLASSES & SECTIONS CREATION
        # ==========================================
        try:
            from academics.models import Standard, Section
            
            # Check if Standard model has date fields
            if hasattr(Standard, 'created_at'):
                new_classes = Standard.objects.filter(
                    created_at_date_range=[seven_days_ago, today]
                )
                
                for std in new_classes:
                    activities.append({
                        "message": f"New class created: Class {std.name}",
                        "who": "Admin",
                        "timestamp": std.created_at.date(),
                        "category": "academic_structure"
                    })
            
            # Check if Section model has date fields
            if hasattr(Section, 'created_at'):
                new_sections = Section.objects.filter(
                    created_at_date_range=[seven_days_ago, today]
                ).select_related('standard')
                
                for sec in new_sections:
                    activities.append({
                        "message": f"New section created: Class {sec.standard.name}-{sec.name}",
                        "who": "Admin",
                        "timestamp": sec.created_at.date(),
                        "category": "academic_structure"
                    })
        except Exception as e:
            print(f"Academic structure error: {e}")

        # ==========================================
        # 14. TEACHER ALLOCATIONS
        # ==========================================
        try:
            from academics.models import ClassTeacher
            
            # Class Teacher assignments
            if hasattr(ClassTeacher, 'created_at'):
                new_allocations = ClassTeacher.objects.filter(
                    created_at_date_range=[seven_days_ago, today]
                ).select_related('teacher', 'section')
                
                for alloc in new_allocations:
                    activities.append({
                        "message": f"Class Teacher assigned: {alloc.teacher.name} for {alloc.section}",
                        "who": "Admin",
                        "timestamp": alloc.created_at.date(),
                        "category": "teacher_allocation"
                    })
            
            # Subject Teacher allocations from AdminBulkAssignTaskView
            from staff_work.models import WorkAssignment
            
            teacher_assignments = WorkAssignment.objects.filter(
                work_order_created_date_range=[seven_days_ago, today],
                work_order_description_icontains='teach'
            ).select_related('staff')
            
            for assign in teacher_assignments:
                activities.append({
                    "message": f"Subject teaching assigned: {assign.staff.name}",
                    "who": "Admin",
                    "timestamp": assign.work_order.created_date,
                    "category": "teacher_allocation"
                })
        except Exception as e:
            print(f"Teacher allocation error: {e}")

        # ==========================================
        # 15. EXAM CREATION
        # ==========================================
        try:
            from exams.models import ExamTerm, ExamType, ExamSchedule
            
            # Exam Terms
            if hasattr(ExamTerm, 'created_at'):
                new_terms = ExamTerm.objects.filter(
                    created_at_date_range=[seven_days_ago, today]
                )
                
                for term in new_terms:
                    activities.append({
                        "message": f"Exam term created: {term.name}",
                        "who": "Admin",
                        "timestamp": term.created_at.date(),
                        "category": "exam_management"
                    })
            
            # Exam Types
            if hasattr(ExamType, 'created_at'):
                new_exam_types = ExamType.objects.filter(
                    created_at_date_range=[seven_days_ago, today]
                ).select_related('term')
                
                for exam in new_exam_types:
                    activities.append({
                        "message": f"Exam created: {exam.name} ({exam.term.name})",
                        "who": "Admin",
                        "timestamp": exam.created_at.date(),
                        "category": "exam_management"
                    })
            
            # Exam Schedules
            if hasattr(ExamSchedule, 'created_at'):
                new_schedules = ExamSchedule.objects.filter(
                    created_at_date_range=[seven_days_ago, today]
                ).select_related('exam_type')
                
                for schedule in new_schedules:
                    activities.append({
                        "message": f"Exam scheduled: {schedule.exam_type.name}",
                        "who": "Admin",
                        "timestamp": schedule.created_at.date(),
                        "category": "exam_management"
                    })
        except Exception as e:
            print(f"Exam creation error: {e}")

        # ==========================================
        # 16. CLASS TESTS
        # ==========================================
        try:
            from exams.models import ClassTest
            
            if hasattr(ClassTest, 'created_at'):
                new_tests = ClassTest.objects.filter(
                    created_at_date_range=[seven_days_ago, today]
                ).select_related('teacher', 'section', 'subject')
                
                for test in new_tests:
                    activities.append({
                        "message": f"Class test created: {test.test_name} - {test.subject.name} for {test.section}",
                        "who": test.teacher.name,
                        "timestamp": test.created_at.date(),
                        "category": "exam_management"
                    })
        except Exception as e:
            print(f"Class test error: {e}")

        # ==========================================
        # 17. RECURRING TASKS
        # ==========================================
        try:
            from staff_work.models import RecurringTaskTemplate
            
            if hasattr(RecurringTaskTemplate, 'created_at'):
                new_templates = RecurringTaskTemplate.objects.filter(
                    created_at_date_range=[seven_days_ago, today]
                ).select_related('staff')
                
                for template in new_templates:
                    activities.append({
                        "message": f"Recurring task setup: {template.description} for {template.staff.name} on {template.day_of_week}",
                        "who": "Admin",
                        "timestamp": template.created_at.date(),
                        "category": "staff_work"
                    })
        except Exception as e:
            print(f"Recurring task error: {e}")

        # ==========================================
        # 18. ATTENDANCE CONFIGURATION
        # ==========================================
        try:
            from attendance.models import AttendanceConfig
            
            if hasattr(AttendanceConfig, 'last_updated'):
                config_updates = AttendanceConfig.objects.filter(
                    last_updated_date_range=[seven_days_ago, today]
                )
                
                for config in config_updates:
                    activities.append({
                        "message": "Attendance settings updated (geofence/timing)",
                        "who": "Admin",
                        "timestamp": config.last_updated.date(),
                        "category": "attendance_config"
                    })
        except Exception as e:
            print(f"Attendance config error: {e}")

        # ==========================================
        # Filter out None timestamps and sort
        # ==========================================
        activities = [a for a in activities if a['timestamp']]
        activities.sort(key=lambda x: x['timestamp'], reverse=True)
        
        # Group by date (last 7 days only)
        grouped_activities = defaultdict(list)
        for activity in activities:
            if activity['timestamp'] >= seven_days_ago:
                date_str = activity['timestamp'].strftime('%Y-%m-%d')
                grouped_activities[date_str].append({
                    "message": activity['message'],
                    "who": activity['who'],
                    "category": activity['category']
                })
        
        # Convert to list format (newest dates first)
        result = []
        for date_str in sorted(grouped_activities.keys(), reverse=True):
            date_activities = grouped_activities[date_str]
            result.append({
                "date": date_str,
                "count": len(date_activities),
                "activities": date_activities[:15]  # Limit per day
            })
        
        return Response({
            "status": 200,
            "period": f"{seven_days_ago} to {today}",
            "total_activities": len(activities),
            "activities_by_date": result
        })


class AdminTeacherOverviewView(APIView):
    """
    Admin-only teacher overview for current academic year.
    Query params: ?teacher_id=TCH001
    """
    permission_classes = [IsAuthenticated, IsAdmin]

    def get(self, request):
        teacher_id = (request.query_params.get('teacher_id') or "").strip()
        if not teacher_id:
            return Response({
                "status": 400,
                "error": "teacher_id parameter is required",
                "data": None
            }, status=400)

        try:
            teacher = scope_queryset_for_user(Teacher.objects.all(), request).get(teacher_id=teacher_id)
        except Teacher.DoesNotExist:
            return Response({
                "status": 404,
                "error": f"Teacher with ID '{teacher_id}' not found",
                "data": None
            }, status=404)

        try:
            joining_date = teacher.joining_date
            today = timezone.now().date()
            today_day_name = today.strftime("%A")
            active_year = get_active_academic_year(request)
            all_years = list(scope_queryset_for_user(AcademicYear.objects.all(), request).order_by('-start_date'))
            if not all_years:
                return Response({
                    "status": 404,
                    "error": "No Academic Years configured",
                    "data": {
                        "current_academic_year": None,
                        "personal_details": {
                            "teacher_id": teacher.teacher_id,
                            "name": teacher.name,
                            "profile_image": teacher.profile_image.url if teacher.profile_image else None
                        },
                        "academic_year_reports": [],
                        "empty_state": {
                            "has_reports": False,
                            "message": "No Academic Years configured"
                        }
                    }
                }, status=404)

            years_to_report = all_years
            if active_year and joining_date and active_year.start_date <= joining_date <= active_year.end_date:
                # If joined in current academic year, show only current-year report.
                years_to_report = [active_year]

            today_attendance = TeacherAttendance.objects.filter(
                teacher=teacher,
                date=today
            ).first()
            today_slots_qs = TimetableSlot.objects.none()
            if active_year:
                today_slots_qs = TimetableSlot.objects.filter(
                    academic_year=active_year,
                    teacher=teacher,
                    day=today_day_name
                ).select_related('section', 'section__standard', 'subject').order_by('period_no', 'start_time')

            reports = []
            for year in years_to_report:
                class_teacher = ClassTeacher.objects.filter(
                    teacher=teacher,
                    academic_year=year
                ).select_related('section', 'section__standard').first()

                section_slots = TimetableSlot.objects.filter(
                    academic_year=year,
                    teacher=teacher
                ).select_related('section', 'section__standard')

                unique_sections = []
                seen = set()
                for slot in section_slots:
                    if not slot.section:
                        continue
                    key = (slot.section.standard.name if slot.section.standard else None, slot.section.name)
                    if key in seen:
                        continue
                    seen.add(key)
                    unique_sections.append({
                        "class": slot.section.standard.name if slot.section.standard else None,
                        "section": slot.section.name
                    })

                allocations = TeacherAllocation.objects.filter(
                    teacher=teacher,
                    academic_year=year
                ).select_related('subject', 'standard')
                handled_standard_ids = sorted({a.standard_id for a in allocations if a.standard_id})
                handled_subject_ids = sorted({a.subject_id for a in allocations if a.subject_id})
                handled_subject_names = sorted({a.subject.name for a in allocations if a.subject and a.subject.name})
                handled_subject_names_lower = {name.strip().lower() for name in handled_subject_names if name}
                handled_class_names = sorted({a.standard.name for a in allocations if a.standard and a.standard.name})

                period_start = year.start_date
                if joining_date:
                    period_start = max(period_start, joining_date)
                period_end = min(year.end_date, today)

                attendance_mode = "academic_year_from_joining_date" if joining_date else "full_academic_year"
                if period_start <= period_end:
                    attendance_qs = TeacherAttendance.objects.filter(
                        teacher=teacher,
                        date__range=[period_start, period_end]
                    ).order_by('-date')
                else:
                    attendance_qs = TeacherAttendance.objects.none()

                s_cnt = 0
                h_cnt = 0
                p_cnt = 0
                l_cnt = 0
                a_cnt = 0
                total_days_passed = 0
                actual_working_days = 0
                holiday_dates = []

                if period_start <= period_end:
                    records_in_period = TeacherAttendance.objects.filter(
                        teacher=teacher,
                        date__range=[period_start, period_end]
                    )
                    att_map = {r.date: r for r in records_in_period}

                    hols = Holiday.objects.filter(
                        date__range=[period_start, period_end]
                    ).filter(Q(applicable_for='everyone') | Q(applicable_for='teachers'))
                    hol_list = list(hols.values_list('date', flat=True))
                    hol_set = set(hol_list)

                    curr = period_start
                    while curr <= period_end:
                        rec = att_map.get(curr)
                        if rec:
                            normalized_status = (rec.status or "").strip().lower()
                            if normalized_status == 'present':
                                p_cnt += 1
                            elif normalized_status == 'late':
                                l_cnt += 1
                            elif normalized_status == 'absent':
                                a_cnt += 1
                            else:
                                a_cnt += 1
                        elif curr.weekday() == 6:
                            s_cnt += 1
                        elif curr in hol_set:
                            h_cnt += 1
                        else:
                            a_cnt += 1
                        curr += timedelta(days=1)

                    total_days_passed = (period_end - period_start).days + 1
                    actual_working_days = max(0, total_days_passed - s_cnt - h_cnt)
                    holiday_dates = [h.strftime("%Y-%m-%d") for h in hol_list]

                total_days_marked = attendance_qs.count()

                if period_start > period_end:
                    attendance_note = "No attendance data available for this academic year period."
                elif joining_date:
                    attendance_note = "Attendance calculated from joining date for this academic year (including Sundays and holidays)."
                else:
                    attendance_note = "Joining date is not set. Attendance calculated for the full academic year period (including Sundays and holidays)."

                upcoming_exam_details = []
                current_exam_details = []
                finished_exam_details = []
                due_details_count = 0
                uploaded_details_count = 0
                pending_details_count = 0

                try:
                    exam_schedules = ExamSchedule.objects.filter(
                        academic_year=year,
                        classes__in=handled_standard_ids
                    ).select_related(
                        'exam_type', 'exam_type__term'
                    ).prefetch_related(
                        'details', 'classes'
                    ).distinct().order_by('start_date', 'exam_type__term__rank', 'exam_type__rank')

                    for schedule in exam_schedules:
                        schedule_class_ids = list(
                            schedule.classes.filter(id__in=handled_standard_ids).values_list('id', flat=True)
                        )
                        if not schedule_class_ids:
                            continue

                        for detail in schedule.details.all().order_by('exam_date'):
                            if handled_subject_names_lower and (detail.subject_name or "").strip().lower() not in handled_subject_names_lower:
                                continue

                            detail_item = {
                                "exam_type": schedule.exam_type.name if schedule.exam_type else None,
                                "term": schedule.exam_type.term.name if schedule.exam_type and schedule.exam_type.term else None,
                                "subject": detail.subject_name,
                                "exam_date": detail.exam_date.isoformat() if detail.exam_date else None,
                                "session": detail.session,
                                "duration": detail.duration,
                                "class_count": len(schedule_class_ids)
                            }

                            if detail.exam_date and detail.exam_date > today:
                                upcoming_exam_details.append(detail_item)
                            elif detail.exam_date and detail.exam_date == today:
                                current_exam_details.append(detail_item)
                            else:
                                finished_exam_details.append(detail_item)

                            if detail.exam_date and detail.exam_date <= today:
                                due_details_count += 1
                                expected_students = Enrollment.objects.filter(
                                    academic_year=year,
                                    is_active=True,
                                    standard_id__in=schedule_class_ids
                                ).count()
                                uploaded_entries = StudentMark.objects.filter(
                                    enrollment__academic_year=year,
                                    enrollment__is_active=True,
                                    enrollment__standard_id__in=schedule_class_ids,
                                    schedule=schedule,
                                    subject__name__iexact=detail.subject_name
                                ).count()
                                if expected_students > 0 and uploaded_entries >= expected_students:
                                    uploaded_details_count += 1
                                else:
                                    pending_details_count += 1
                except Exception:
                    logger.exception(
                        "Exam/performance section failed in AdminTeacherOverviewView for teacher_id=%s, year=%s",
                        teacher_id,
                        getattr(year, "name", None),
                    )

                marks_qs = StudentMark.objects.filter(
                    enrollment__academic_year=year,
                    enrollment__is_active=True,
                    enrollment__standard_id__in=handled_standard_ids,
                    subject_id__in=handled_subject_ids
                ) if handled_standard_ids and handled_subject_ids else StudentMark.objects.none()
                marks_totals = marks_qs.aggregate(
                    total_entries=Count('id'),
                    obtained=Sum('marks_obtained'),
                    maximum=Sum('total_marks')
                )
                total_entries = marks_totals.get('total_entries') or 0
                total_obtained = float(marks_totals.get('obtained') or 0)
                total_maximum = float(marks_totals.get('maximum') or 0)
                overall_marks_percentage = round((total_obtained / total_maximum) * 100, 1) if total_maximum > 0 else 0.0
                avg_entry_percentage = round((total_obtained / total_entries), 1) if total_entries > 0 else 0.0

                total_exam_details = len(upcoming_exam_details) + len(current_exam_details) + len(finished_exam_details)
                upload_completion_percentage = round((uploaded_details_count / due_details_count) * 100, 1) if due_details_count > 0 else 0.0

                reports.append({
                    "academic_year": year.name,
                    "is_current": bool(year.is_current),
                    "date_range": {
                        "start_date": year.start_date.isoformat() if year.start_date else None,
                        "end_date": year.end_date.isoformat() if year.end_date else None
                    },
                    "class_information": {
                        "is_class_teacher": bool(class_teacher),
                        "class_teacher_of": {
                            "class": class_teacher.section.standard.name if class_teacher and class_teacher.section and class_teacher.section.standard else None,
                            "section": class_teacher.section.name if class_teacher and class_teacher.section else None
                        },
                        "sections_teaching": unique_sections or []
                    },
                    "allocations": [{
                        "subject_name": alloc.subject.name if alloc.subject else None,
                        "subject_code": alloc.subject.subject_code if alloc.subject else None,
                        "class": alloc.standard.name if alloc.standard else None
                    } for alloc in allocations],
                    "attendance_history": {
                        "period": year.name,
                        "from_date": period_start.isoformat() if period_start <= period_end else None,
                        "to_date": period_end.isoformat() if period_start <= period_end else None,
                        "based_on_joining_date": bool(joining_date),
                        "mode": attendance_mode,
                        "note": attendance_note,
                        "summary": {
                            "total_days_marked": total_days_marked,
                            "total_days_passed": total_days_passed,
                            "actual_working_days": actual_working_days,
                            "sundays": s_cnt,
                            "holidays": h_cnt,
                            "holiday_dates": holiday_dates,
                            "present": p_cnt,
                            "late": l_cnt,
                            "absent": a_cnt,
                            "present_days": p_cnt,
                            "late_days": l_cnt,
                            "absent_days": a_cnt,
                            "percentage": f"{round(((p_cnt + l_cnt) / actual_working_days) * 100, 1)}%" if actual_working_days > 0 else "0%"
                        }
                    },
                    "exam_and_performance": {
                        "handled_subjects": handled_subject_names,
                        "handled_classes": handled_class_names,
                        "exam_counts": {
                            "total_scheduled": total_exam_details,
                            "upcoming": len(upcoming_exam_details),
                            "current": len(current_exam_details),
                            "finished": len(finished_exam_details)
                        },
                        "mark_upload_status": {
                            "due_by_exam_date": due_details_count,
                            "uploaded": uploaded_details_count,
                            "pending": pending_details_count,
                            "upload_percentage": upload_completion_percentage
                        },
                        "overall_marks": {
                            "entries_count": total_entries,
                            "total_obtained_marks": total_obtained,
                            "total_max_marks": total_maximum,
                            "overall_percentage": overall_marks_percentage,
                            "average_marks_per_entry": avg_entry_percentage
                        },
                        "exam_details": {
                            "upcoming": upcoming_exam_details,
                            "current": current_exam_details,
                            "finished": finished_exam_details
                        }
                    }
                })

            transport_data = self.get_transport_details(teacher)

            response_data = {
                "status": 200,
                "message": "Teacher overview retrieved successfully",
                "data": {
                    "current_academic_year": active_year.name if active_year else None,
                    "personal_details": {
                        "teacher_id": teacher.teacher_id,
                        "name": teacher.name,
                        "email": teacher.email,
                        "phone": teacher.phone,
                        "department": teacher.department,
                        "qualification": teacher.qualification,
                        "date_of_birth": teacher.date_of_birth.isoformat() if teacher.date_of_birth else None,
                        "joining_date": joining_date.isoformat() if joining_date else None,
                        "address": teacher.address,
                        "profile_image": teacher.profile_image.url if teacher.profile_image else None,
                        "bank_account_number": teacher.bank_account_number,
                        "ifsc_code": teacher.ifsc_code,
                        "account_holder_name": teacher.account_holder_name,
                        "bank_name": teacher.bank_name,
                        "upi_id": teacher.upi_id,
                        "extra_details": teacher.extra_details if teacher.extra_details else {}
                    },
                    "full_teacher_details": {
                        "teacher_id": teacher.teacher_id,
                        "name": teacher.name,
                        "email": teacher.email,
                        "phone": teacher.phone,
                        "department": teacher.department,
                        "qualification": teacher.qualification,
                        "date_of_birth": teacher.date_of_birth.isoformat() if teacher.date_of_birth else None,
                        "joining_date": joining_date.isoformat() if joining_date else None,
                        "address": teacher.address or "Not Provided",
                        "profile_image": teacher.profile_image.url if teacher.profile_image else None,
                        "assigned_class": teacher.current_class,
                        "bank_details": {
                            "account_holder_name": teacher.account_holder_name,
                            "bank_name": teacher.bank_name,
                            "bank_account_number": teacher.bank_account_number,
                            "ifsc_code": teacher.ifsc_code,
                            "upi_id": teacher.upi_id
                        },
                        "extra_details": teacher.extra_details if teacher.extra_details else {},
                        "today_attendance": today_attendance.status if today_attendance else "Not Marked",
                        "today_check_in_time": (
                            today_attendance.check_in_time.isoformat()
                            if today_attendance and today_attendance.check_in_time else None
                        )
                    },
                    "today_snapshot": {
                        "date": today.isoformat(),
                        "today_attendance_status": today_attendance.status if today_attendance else "Not Marked",
                        "attendance": {
                            "status": today_attendance.status if today_attendance else "Not Marked",
                            "check_in_time": today_attendance.check_in_time.isoformat() if today_attendance and today_attendance.check_in_time else None
                        },
                        "classes": {
                            "day": today_day_name,
                            "count": today_slots_qs.count(),
                            "list": [{
                                "period_no": slot.period_no,
                                "start_time": slot.start_time.isoformat() if slot.start_time else None,
                                "end_time": slot.end_time.isoformat() if slot.end_time else None,
                                "subject": slot.subject.name if slot.subject else None,
                                "class": slot.section.standard.name if slot.section and slot.section.standard else None,
                                "section": slot.section.name if slot.section else None
                            } for slot in today_slots_qs]
                        }
                    },
                    "transport": transport_data,
                    "academic_year_reports": reports
                }
            }

            if not response_data["data"]["academic_year_reports"]:
                response_data["data"]["academic_year_reports"] = []
                response_data["data"]["empty_state"] = {
                    "has_reports": False,
                    "message": "No academic year report data available for this teacher yet."
                }
            else:
                response_data["data"]["empty_state"] = {
                    "has_reports": True,
                    "message": None
                }

            return Response(response_data, status=200)

        except Exception as e:
            logger.exception("Error in AdminTeacherOverviewView for teacher_id=%s", teacher_id)
            return Response({
                "status": 500,
                "error": "An error occurred while fetching teacher overview",
                "message": str(e),
                "data": None
            }, status=500)

    def get_transport_details(self, teacher):
        """Get teacher transport allocation details and today's bus attendance."""
        today = timezone.now().date()

        default_payload = {
            "is_assigned": False,
            "message": "Teacher is not assigned to any bus",
            "bus": None,
            "stop": None,
            "today_bus_attendance": {
                "date": today.isoformat(),
                "morning": {
                    "is_marked": False,
                    "status": "Not Marked",
                    "marked_by": None,
                    "updated_at": None
                },
                "evening": {
                    "is_marked": False,
                    "status": "Not Marked",
                    "marked_by": None,
                    "updated_at": None
                },
                "overall_status": "Not Marked"
            }
        }

        try:
            allocation = (
                TransportAllocation.objects
                .filter(teacher=teacher)
                .select_related('vehicle__driver', 'vehicle__route', 'stop')
                .first()
            )
            if not allocation or not allocation.vehicle:
                return default_payload

            vehicle = allocation.vehicle
            route = getattr(vehicle, 'route', None)
            stop = allocation.stop

            attendance_records = TransportAttendance.objects.filter(
                allocation=allocation,
                date=today
            ).select_related('marked_by')
            morning_rec = attendance_records.filter(trip_type='Morning').first()
            evening_rec = attendance_records.filter(trip_type='Evening').first()

            morning_status = morning_rec.status if morning_rec else "Not Marked"
            evening_status = evening_rec.status if evening_rec else "Not Marked"

            if morning_rec and evening_rec:
                if morning_status == "Present" and evening_status == "Present":
                    overall_status = "Present"
                elif morning_status == "Absent" and evening_status == "Absent":
                    overall_status = "Absent"
                else:
                    overall_status = "Partially Present"
            elif morning_rec or evening_rec:
                overall_status = "Partially Marked"
            else:
                overall_status = "Not Marked"

            return {
                "is_assigned": True,
                "message": "Transport details retrieved successfully",
                "bus": {
                    "bus_id": vehicle.id,
                    "bus_number": vehicle.bus_number,
                    "registration_number": vehicle.registration_number,
                    "capacity": vehicle.capacity,
                    "driver_name": vehicle.driver.name if vehicle.driver else None,
                    "route_id": route.id if route else None,
                    "route_start": route.start_location if route else None,
                    "route_end": route.end_location if route else None,
                    "is_live": bool(route.is_active) if route else False
                },
                "stop": {
                    "stop_id": stop.id if stop else None,
                    "stop_name": stop.stop_name if stop else None,
                    "order_number": stop.order_number if stop else None,
                    "arrival_time": stop.arrival_time.strftime("%H:%M:%S") if stop and stop.arrival_time else None,
                    "latitude": stop.latitude if stop else None,
                    "longitude": stop.longitude if stop else None
                },
                "today_bus_attendance": {
                    "date": today.isoformat(),
                    "morning": {
                        "is_marked": bool(morning_rec),
                        "status": morning_status,
                        "marked_by": morning_rec.marked_by.name if morning_rec and morning_rec.marked_by else None,
                        "updated_at": morning_rec.updated_at if morning_rec else None
                    },
                    "evening": {
                        "is_marked": bool(evening_rec),
                        "status": evening_status,
                        "marked_by": evening_rec.marked_by.name if evening_rec and evening_rec.marked_by else None,
                        "updated_at": evening_rec.updated_at if evening_rec else None
                    },
                    "overall_status": overall_status
                }
            }
        except Exception:
            logger.exception("Error in get_transport_details for teacher_id=%s", getattr(teacher, "teacher_id", None))
            return {
                **default_payload,
                "message": "Unable to load transport details"
            }

class AdminStaffOverviewView(APIView):
    """
    Admin-only staff overview for all academic years.
    Query params: ?staff_id=STF001
    """
    permission_classes = [IsAuthenticated, IsAdmin]

    def get(self, request):
        staff_id = (request.query_params.get('staff_id') or "").strip()
        if not staff_id:
            return Response({
                "status": 400,
                "error": "staff_id parameter is required",
                "data": None
            }, status=400)

        try:
            staff_member = scope_queryset_for_user(NonTeachingStaff.objects.all(), request).get(staff_id=staff_id)
        except NonTeachingStaff.DoesNotExist:
            return Response({
                "status": 404,
                "error": f"Staff with ID '{staff_id}' not found",
                "data": None
            }, status=404)

        try:
            joining_date = staff_member.joining_date
            today = timezone.now().date()
            active_year = get_active_academic_year(request)
            all_years = list(scope_queryset_for_user(AcademicYear.objects.all(), request).order_by('-start_date'))
            if not all_years:
                return Response({
                    "status": 404,
                    "error": "No Academic Years configured",
                    "data": {
                        "current_academic_year": None,
                        "personal_details": {
                            "staff_id": staff_member.staff_id,
                            "name": staff_member.name,
                            "profile_image": staff_member.profile_image.url if staff_member.profile_image else None
                        },
                        "academic_year_reports": [],
                        "empty_state": {
                            "has_reports": False,
                            "message": "No Academic Years configured"
                        }
                    }
                }, status=404)

            years_to_report = all_years
            if active_year and joining_date and active_year.start_date <= joining_date <= active_year.end_date:
                years_to_report = [active_year]

            is_transport_staff = (staff_member.role == 'transport_staff')
            driver_vehicle = Vehicle.objects.filter(
                driver=staff_member
            ).select_related('route', 'driver').first() if is_transport_staff else None

            transport_alloc = None
            if not is_transport_staff:
                transport_alloc = TransportAllocation.objects.filter(
                    staff=staff_member
                ).select_related(
                    'vehicle',
                    'vehicle__route',
                    'vehicle__driver',
                    'stop'
                ).order_by('-allocated_at', '-id').first()

            selected_vehicle = driver_vehicle or (transport_alloc.vehicle if transport_alloc and transport_alloc.vehicle else None)
            assignment_mode = "driver" if driver_vehicle else ("allocated_staff" if selected_vehicle else None)
            staff_role = staff_member.role or ""
            staff_role_display = staff_member.get_role_display()
            profile_image_url = staff_member.profile_image.url if staff_member.profile_image else None


            transport_details = {
                "is_allocated": bool(selected_vehicle),
                "assignment_mode": assignment_mode,
                "is_driver_assigned": bool(driver_vehicle),
                "allocation_id": transport_alloc.id if (assignment_mode == "allocated_staff" and transport_alloc) else None,
                "user_type": transport_alloc.user_type if (assignment_mode == "allocated_staff" and transport_alloc) else None,
                "allocated_at": transport_alloc.allocated_at.isoformat() if (assignment_mode == "allocated_staff" and transport_alloc and transport_alloc.allocated_at) else None,
                "bus_number": selected_vehicle.bus_number if selected_vehicle else None,
                "registration_number": selected_vehicle.registration_number if selected_vehicle else None,
                "capacity": selected_vehicle.capacity if selected_vehicle else None,
                "stop": {
                    "stop_id": transport_alloc.stop.id if (transport_alloc and transport_alloc.stop) else None,
                    "stop_name": transport_alloc.stop.stop_name if (transport_alloc and transport_alloc.stop) else None
                },
                "driver": {
                    "staff_id": selected_vehicle.driver.staff_id if selected_vehicle and selected_vehicle.driver else None,
                    "name": selected_vehicle.driver.name if selected_vehicle and selected_vehicle.driver else None
                },
                "route": {
                    "start_location": selected_vehicle.route.start_location if selected_vehicle and getattr(selected_vehicle, 'route', None) else None,
                    "end_location": selected_vehicle.route.end_location if selected_vehicle and getattr(selected_vehicle, 'route', None) else None,
                    "is_active": bool(selected_vehicle.route.is_active) if selected_vehicle and getattr(selected_vehicle, 'route', None) else False,
                    "last_updated": selected_vehicle.route.last_updated.isoformat() if selected_vehicle and getattr(selected_vehicle, 'route', None) and selected_vehicle.route.last_updated else None
                },
                "note": (
                    "Staff is assigned as bus driver."
                    if assignment_mode == "driver"
                    else "Staff transport allocation found."
                    if assignment_mode == "allocated_staff"
                    else "No transport assignment found for this staff."
                )
            }
            today_assignment_qs = WorkAssignment.objects.filter(
                staff=staff_member,
                work_order__created_date=today
            ).select_related('work_order').order_by('-id')
            today_inventory_qs = StockLog.objects.filter(
                staff=staff_member,
                timestamp__date=today
            ).select_related('item').order_by('-timestamp')
            today_attendance = StaffAttendance.objects.filter(
                staff=staff_member,
                date=today
            ).first()
            reports = []

            for year in years_to_report:
                period_start = year.start_date
                if joining_date:
                    period_start = max(period_start, joining_date)
                period_end = min(year.end_date, today)

                if period_start <= period_end:
                    attendance_qs = StaffAttendance.objects.filter(
                        staff=staff_member,
                        date__range=[period_start, period_end]
                    )
                else:
                    attendance_qs = StaffAttendance.objects.none()

                work_qs = WorkAssignment.objects.filter(
                    staff=staff_member,
                    work_order__created_date__range=[period_start, period_end]
                ) if period_start <= period_end else WorkAssignment.objects.none()

                attendance_mode = "academic_year_from_joining_date" if joining_date else "full_academic_year"
                total_days_passed = 0
                actual_working_days = 0
                sundays = 0
                holidays = 0
                holiday_dates = []
                present_days = 0
                late_days = 0
                absent_days = 0
                total_days_marked = attendance_qs.count()

                if period_start <= period_end:
                    attendance_map = {r.date: r for r in attendance_qs}
                    holiday_qs = Holiday.objects.filter(
                        date__range=[period_start, period_end]
                    ).filter(Q(applicable_for='everyone') | Q(applicable_for='staff'))
                    holiday_list = list(holiday_qs.values_list('date', flat=True))
                    holiday_set = set(holiday_list)

                    current_date = period_start
                    while current_date <= period_end:
                        rec = attendance_map.get(current_date)
                        if rec:
                            status_key = (rec.status or "").strip().lower()
                            if status_key == 'present':
                                present_days += 1
                            elif status_key == 'late':
                                late_days += 1
                            elif status_key == 'absent':
                                absent_days += 1
                            else:
                                absent_days += 1
                        elif current_date.weekday() == 6:
                            sundays += 1
                        elif current_date in holiday_set:
                            holidays += 1
                        else:
                            absent_days += 1
                        current_date += timedelta(days=1)

                    total_days_passed = (period_end - period_start).days + 1
                    actual_working_days = max(0, total_days_passed - sundays - holidays)
                    holiday_dates = [d.strftime("%Y-%m-%d") for d in holiday_list]

                if period_start > period_end:
                    attendance_note = "No attendance data available for this academic year period."
                elif joining_date:
                    attendance_note = "Attendance calculated from joining date for this academic year (including Sundays and holidays)."
                else:
                    attendance_note = "Joining date is not set. Attendance calculated for the full academic year period (including Sundays and holidays)."

                work_total = work_qs.count()
                work_pending = work_qs.filter(status__iexact='Pending').count()
                work_completed = work_qs.filter(status__iexact='Completed').count()
                work_list_qs = work_qs.select_related('work_order').order_by('-work_order__created_date', '-id')
                work_list = [{
                    "assignment_id": item.id,
                    "description": item.work_order.description if item.work_order else "",
                    "status": item.status,
                    "is_recurring": bool(item.work_order.is_recurring) if item.work_order else False,
                    "created_date": item.work_order.created_date.isoformat() if item.work_order and item.work_order.created_date else None,
                    "completed_at": item.completed_at.isoformat() if item.completed_at else None,
                    "proof_uploaded": bool(item.proof_file)
                } for item in work_list_qs[:25]]
                work_updates = [{
                    "assignment_id": item.id,
                    "update_type": "completed" if item.completed_at else "assigned",
                    "status": item.status,
                    "description": item.work_order.description if item.work_order else "",
                    "updated_at": item.completed_at.isoformat() if item.completed_at else (item.work_order.created_date.isoformat() if item.work_order and item.work_order.created_date else None)
                } for item in work_list_qs[:10]]
                inventory_qs = StockLog.objects.filter(
                    staff=staff_member,
                    timestamp__date__range=[period_start, period_end]
                ).select_related('item').order_by('-timestamp') if period_start <= period_end else StockLog.objects.none()
                inventory_total = inventory_qs.count()
                inventory_used = inventory_qs.filter(action='used').count()
                inventory_damaged = inventory_qs.filter(action='damaged').count()
                inventory_restocked = inventory_qs.filter(action='restocked').count()
                inventory_logs = [{
                    "log_id": log.id,
                    "item_name": log.item.stock_name if log.item else None,
                    "action": log.action,
                    "quantity_changed": log.quantity_changed,
                    "timestamp": log.timestamp.isoformat() if log.timestamp else None
                } for log in inventory_qs[:10]]

                report_item = {
                    "academic_year": year.name,
                    "is_current": bool(year.is_current),
                    "date_range": {
                        "start_date": year.start_date.isoformat() if year.start_date else None,
                        "end_date": year.end_date.isoformat() if year.end_date else None
                    },
                    "attendance": {
                        "period": year.name,
                        "from_date": period_start.isoformat() if period_start <= period_end else None,
                        "to_date": period_end.isoformat() if period_start <= period_end else None,
                        "based_on_joining_date": bool(joining_date),
                        "mode": attendance_mode,
                        "note": attendance_note,
                        "summary": {
                            "total_days_marked": total_days_marked,
                            "total_days_passed": total_days_passed,
                            "actual_working_days": actual_working_days,
                            "sundays": sundays,
                            "holidays": holidays,
                            "holiday_dates": holiday_dates,
                            "present": present_days,
                            "late": late_days,
                            "absent": absent_days,
                            "attendance_percentage": round(((present_days + late_days) / actual_working_days) * 100, 1) if actual_working_days > 0 else 0.0
                        }
                    },
                    "transport": transport_details
                }
                report_item["works"] = {
                    "total_assignments": work_total,
                    "work_status_counts": {
                        "pending": work_pending,
                        "completed": work_completed
                    },
                    "list": work_list,
                    "updates": work_updates
                }
                report_item["inventory"] = {
                    "has_activity": inventory_total > 0,
                    "summary": {
                        "total_logs": inventory_total,
                        "used": inventory_used,
                        "damaged": inventory_damaged,
                        "restocked": inventory_restocked
                    },
                    "logs": inventory_logs
                }
                reports.append(report_item)

            response_data = {
                "status": 200,
                "message": "Staff overview retrieved successfully",
                "data": {
                    "current_academic_year": active_year.name if active_year else None,
                    "personal_details": {
                        "staff_id": staff_member.staff_id,
                        "name": staff_member.name,
                        "phone": staff_member.phone,
                        "email": staff_member.email,
                        "role": staff_role,
                        "role_display": staff_role_display,
                        "joining_date": staff_member.joining_date.isoformat() if staff_member.joining_date else None,
                        "address": staff_member.address,
                        "profile_image": profile_image_url,
                        "bank_account_number": staff_member.bank_account_number,
                        "ifsc_code": staff_member.ifsc_code,
                        "account_holder_name": staff_member.account_holder_name,
                        "bank_name": staff_member.bank_name,
                        "upi_id": staff_member.upi_id,
                        "transport_role_flags": {
                            "is_transport_staff": is_transport_staff,
                            "is_bus_driver": bool(driver_vehicle),
                            "assignment_mode": assignment_mode
                        },
                        "extra_details": staff_member.extra_details if staff_member.extra_details else {}
                    },
                    "full_staff_details": {
                        "staff_id": staff_member.staff_id,
                        "name": staff_member.name,
                        "phone": staff_member.phone,
                        "email": staff_member.email,
                        "role": staff_role,
                        "role_display": staff_role_display,
                        "joining_date": staff_member.joining_date.isoformat() if staff_member.joining_date else None,
                        "address": staff_member.address or "Not Provided",
                        "profile_image": profile_image_url,
                        "bank_details": {
                            "account_holder_name": staff_member.account_holder_name,
                            "bank_name": staff_member.bank_name,
                            "bank_account_number": staff_member.bank_account_number,
                            "ifsc_code": staff_member.ifsc_code,
                            "upi_id": staff_member.upi_id
                        },
                        "user_account": {
                            "linked_user_id": staff_member.user_id,
                            "username": staff_member.user.username if staff_member.user else None,
                            "is_active": bool(staff_member.user.is_active) if staff_member.user else None
                        },
                        "role_access": {
                            "role": staff_role,
                            "role_display": staff_role_display,
                            "is_transport_staff": is_transport_staff,
                            "is_bus_driver": bool(driver_vehicle),
                            "assignment_mode": assignment_mode
                        },
                        "extra_details": staff_member.extra_details if staff_member.extra_details else {}
                    },
                    "today_snapshot": {
                        "date": today.isoformat(),
                        "today_attendance_status": today_attendance.status if today_attendance else "Not Marked",
                        "attendance": {
                            "status": today_attendance.status if today_attendance else "Not Marked",
                            "check_in_time": today_attendance.check_in_time.isoformat() if today_attendance and today_attendance.check_in_time else None
                        },
                        "role_insights": {
                            "role": staff_role,
                            "role_display": staff_role_display,
                            "is_transport_staff": is_transport_staff
                        },
                        "transport": transport_details
                    },
                    "transport_details": transport_details,
                    "academic_year_reports": reports
                }
            }

            response_data["data"]["today_snapshot"]["works"] = {
                "total": today_assignment_qs.count(),
                "pending": today_assignment_qs.filter(status__iexact='Pending').count(),
                "completed": today_assignment_qs.filter(status__iexact='Completed').count(),
                "list": [{
                    "assignment_id": item.id,
                    "description": item.work_order.description if item.work_order else "",
                    "status": item.status,
                    "is_recurring": bool(item.work_order.is_recurring) if item.work_order else False,
                    "created_date": item.work_order.created_date.isoformat() if item.work_order and item.work_order.created_date else None,
                    "completed_at": item.completed_at.isoformat() if item.completed_at else None,
                    "proof_uploaded": bool(item.proof_file)
                } for item in today_assignment_qs],
                "updates": [{
                    "assignment_id": item.id,
                    "update_type": "completed" if item.completed_at else "assigned",
                    "status": item.status,
                    "description": item.work_order.description if item.work_order else "",
                    "updated_at": item.completed_at.isoformat() if item.completed_at else (item.work_order.created_date.isoformat() if item.work_order and item.work_order.created_date else None)
                } for item in today_assignment_qs[:10]]
            }
            response_data["data"]["today_snapshot"]["today_work_updates"] = response_data["data"]["today_snapshot"]["works"]["updates"]
            response_data["data"]["today_snapshot"]["inventory"] = {
                "total_logs": today_inventory_qs.count(),
                "logs": [{
                    "log_id": log.id,
                    "item_name": log.item.stock_name if log.item else None,
                    "action": log.action,
                    "quantity_changed": log.quantity_changed,
                    "timestamp": log.timestamp.isoformat() if log.timestamp else None
                } for log in today_inventory_qs[:10]]
            }

            if not response_data["data"]["academic_year_reports"]:
                response_data["data"]["academic_year_reports"] = []
                response_data["data"]["empty_state"] = {
                    "has_reports": False,
                    "message": "No academic year report data available for this staff yet."
                }
            else:
                response_data["data"]["empty_state"] = {
                    "has_reports": True,
                    "message": None
                }

            return Response(response_data, status=200)
        except Exception as e:
            logger.exception("Error in AdminStaffOverviewView for staff_id=%s", staff_id)
            return Response({
                "status": 500,
                "error": "An error occurred while fetching staff overview",
                "message": str(e),
                "data": None
            }, status=500)
        

class DashboardStatsView(APIView):
    """
    Simplified dashboard statistics API.
    Returns: Basic student, teacher, staff, and academic statistics.
    """
    permission_classes = [IsAuthenticated, IsAdmin]

    def get(self, request):
        try:
            # Get active academic year
            active_year = AcademicYear.objects.get(is_current=True)
        except AcademicYear.DoesNotExist:
            return Response({"error": "No active academic year found"}, status=500)
        
        # Get today's date
        today = timezone.now().date()
        
        # ==========================================
        # 1. STUDENT STATISTICS
        # ==========================================
        
        # Get active enrollments for current year
        active_enrollments = Enrollment.objects.filter(
            academic_year=active_year,
            is_active=True
        )
        
        total_students = active_enrollments.count()
        
        # Gender distribution
        gender_stats = active_enrollments.values('student__gender').annotate(
            count=Count('id')
        )
        
        male_count = 0
        female_count = 0
        other_gender_count = 0
        
        for stat in gender_stats:
            gender = stat['student__gender'] or ''
            if gender.lower() in ['male', 'm']:
                male_count = stat['count']
            elif gender.lower() in ['female', 'f']:
                female_count = stat['count']
            else:
                other_gender_count += stat['count']
        
        # Today's student attendance
        today_attendance = Attendance.objects.filter(
            enrollment__academic_year=active_year,
            date=today
        )
        
        today_attendance_stats = today_attendance.aggregate(
            present=Count('id', filter=Q(status__iexact='present')),
            absent=Count('id', filter=Q(status__iexact='absent')),
            late=Count('id', filter=Q(status__iexact='late'))
        )
        
        total_marked_today = (
            (today_attendance_stats['present'] or 0) + 
            (today_attendance_stats['absent'] or 0) + 
            (today_attendance_stats['late'] or 0)
        )
        
        attendance_percentage_today = 0
        if total_marked_today > 0 and total_students > 0:
            attendance_percentage_today = (total_marked_today / total_students) * 100
        
        # Monthly attendance
        current_month = today.month
        current_year = today.year
        
        month_attendance_stats = Attendance.objects.filter(
            enrollment__academic_year=active_year,
            date__year=current_year,
            date__month=current_month
        ).aggregate(
            present=Count('id', filter=Q(status__iexact='present')),
            absent=Count('id', filter=Q(status__iexact='absent')),
            late=Count('id', filter=Q(status__iexact='late')),
            total=Count('id')
        )
        
        month_present = month_attendance_stats['present'] or 0
        month_absent = month_attendance_stats['absent'] or 0
        month_late = month_attendance_stats['late'] or 0
        month_total = month_attendance_stats['total'] or 0
        
        # Calculate working days so far this month
        total_days_so_far = today.day
        month_working_days = 0
        
        for day in range(1, total_days_so_far + 1):
            check_date = date(current_year, current_month, day)
            if check_date.weekday() != 6:  # Skip Sundays
                month_working_days += 1
        
        overall_percentage = 0
        if month_working_days > 0 and total_students > 0:
            possible_attendance = total_students * month_working_days
            overall_percentage = (month_present / possible_attendance) * 100
        
        # ==========================================
        # 2. TEACHER STATISTICS
        # ==========================================
        
        total_teachers = Teacher.objects.count()
        
        # Today's teacher attendance
        today_teacher_attendance = TeacherAttendance.objects.filter(
            date=today
        )
        
        teacher_attendance_stats = today_teacher_attendance.aggregate(
            present=Count('id', filter=Q(status__iexact='present')),
            late=Count('id', filter=Q(status__iexact='late'))
        )
        
        teacher_present = teacher_attendance_stats['present'] or 0
        teacher_late = teacher_attendance_stats['late'] or 0
        teacher_absent = total_teachers - (teacher_present + teacher_late)
        
        # ==========================================
        # 3. STAFF STATISTICS
        # ==========================================
        
        total_staff = NonTeachingStaff.objects.count()
        
        # Today's staff attendance
        today_staff_attendance = StaffAttendance.objects.filter(
            date=today
        )
        
        staff_attendance_stats = today_staff_attendance.aggregate(
            present=Count('id', filter=Q(status__iexact='present')),
            late=Count('id', filter=Q(status__iexact='late'))
        )
        
        staff_present = staff_attendance_stats['present'] or 0
        staff_late = staff_attendance_stats['late'] or 0
        staff_absent = total_staff - (staff_present + staff_late)
        
        # ==========================================
        # 4. CLASS & SECTION STATISTICS
        # ==========================================
        
        # Get unique classes with active students
        active_classes_qs = active_enrollments.values('standard__name').distinct()
        active_classes_count = active_classes_qs.count()
        
        # Get class-wise student counts
        class_student_counts = active_enrollments.values(
            'standard__name'
        ).annotate(
            student_count=Count('id'),
            section_count=Count('section', distinct=True)
        ).order_by('standard__name')
        
        # Convert to list and sort numerically
        class_data = []
        for item in class_student_counts:
            class_data.append({
                'class_name': item['standard__name'],
                'student_count': item['student_count'],
                'section_count': item['section_count']
            })
        
        # Sort classes numerically
        def sort_class_key(item):
            name = item['class_name']
            try:
                return int(name)
            except ValueError:
                special_order = {
                    'PRE-KG': -3,
                    'LKG': -2,
                    'UKG': -1,
                    'KG': -1.5,
                    'NURSERY': -4,
                    'PLAYGROUP': -5
                }
                return special_order.get(name.upper(), 999)
        
        sorted_class_data = sorted(class_data, key=sort_class_key)
        
        # Count total sections with active students
        active_sections_qs = active_enrollments.values('section__id').distinct()
        active_sections_count = active_sections_qs.count()
        
        # Count total sections defined in system
        total_sections_defined = Section.objects.count()
        total_classes_defined = Standard.objects.count()
        
        # ==========================================
        # 5. ASSEMBLE FINAL RESPONSE
        # ==========================================
        
        response_data = {
            "meta": {
                "date": today.isoformat(),
                "academic_year": active_year.name,
                "generated_at": timezone.now().isoformat(),
                "time_periods": {
                    "daily": today.isoformat(),
                    "weekly": f"{(today - timedelta(days=6)).isoformat()} to {today.isoformat()}",
                    "monthly": f"{current_year}-{current_month:02d} (so far)"
                }
            },
            "students": {
                "total": total_students,
                "gender_distribution": {
                    "male": male_count,
                    "female": female_count,
                    "other": other_gender_count,
                    "male_percentage": round((male_count / total_students * 100), 1) if total_students > 0 else 0,
                    "female_percentage": round((female_count / total_students * 100), 1) if total_students > 0 else 0
                },
                "today": {
                    "present": today_attendance_stats['present'] or 0,
                    "absent": today_attendance_stats['absent'] or 0,
                    "late": today_attendance_stats['late'] or 0,
                    "total_marked": total_marked_today,
                    "unmarked": total_students - total_marked_today,
                    "attendance_percentage": round(attendance_percentage_today, 1),
                    "breakdown_percentage": {
                        "present": round(((today_attendance_stats['present'] or 0) / total_marked_today * 100), 1) if total_marked_today > 0 else 0,
                        "absent": round(((today_attendance_stats['absent'] or 0) / total_marked_today * 100), 1) if total_marked_today > 0 else 0,
                        "late": round(((today_attendance_stats['late'] or 0) / total_marked_today * 100), 1) if total_marked_today > 0 else 0
                    }
                },
                "overall": {
                    "attendance_percentage": round(overall_percentage, 1),
                    "present_this_month": month_present,
                    "absent_this_month": month_absent,
                    "late_this_month": month_late,
                    "total_marked_this_month": month_total,
                    "monthly_summary": {
                        "present_percentage": round((month_present / month_total * 100), 1) if month_total > 0 else 0,
                        "absent_percentage": round((month_absent / month_total * 100), 1) if month_total > 0 else 0,
                        "late_percentage": round((month_late / month_total * 100), 1) if month_total > 0 else 0
                    }
                }
            },
            "teachers": {
                "total": total_teachers,
                "today": {
                    "present": teacher_present,
                    "late": teacher_late,
                    "absent": teacher_absent,
                    "attendance_percentage": round((teacher_present / total_teachers * 100), 1) if total_teachers > 0 else 0,
                    "late_percentage": round((teacher_late / total_teachers * 100), 1) if total_teachers > 0 else 0
                }
            },
            "staff": {
                "total": total_staff,
                "today": {
                    "present": staff_present,
                    "late": staff_late,
                    "absent": staff_absent,
                    "attendance_percentage": round((staff_present / total_staff * 100), 1) if total_staff > 0 else 0,
                    "late_percentage": round((staff_late / total_staff * 100), 1) if total_staff > 0 else 0
                }
            },
            "academics": {
                "total_classes": active_classes_count,
                "total_sections": active_sections_count,
                "class_details": sorted_class_data,
                "system_defined": {
                    "total_classes": total_classes_defined,
                    "total_sections": total_sections_defined
                },
                "inactive": {
                    "classes": total_classes_defined - active_classes_count,
                    "sections": total_sections_defined - active_sections_count
                }
            }
        }
        
        return Response(response_data, status=200)
    
class AdminRecentActivitiesView(APIView):
    """
    GET: Get all recent activities for admin dashboard.
    Shows activities from the last 24 hours.
    """
    permission_classes = [IsAuthenticated, IsAdmin]

    @staticmethod
    def _short_text(value, limit=100):
        if not value:
            return ""
        text = str(value).strip()
        return text if len(text) <= limit else f"{text[:limit].rstrip()}..."

    @staticmethod
    def _transport_passenger_name(allocation):
        if allocation.student:
            return allocation.student.student_name, "Student"
        if allocation.teacher:
            return allocation.teacher.name, "Teacher"
        if allocation.staff:
            role = allocation.staff.role or "Staff"
            return allocation.staff.name, f"Staff ({role})"
        return "Passenger", "Transport"

    @staticmethod
    def _leave_requester(leave_request):
        if leave_request.student:
            return leave_request.student.student_name, "Student"
        if leave_request.teacher:
            return leave_request.teacher.name, "Teacher"
        if leave_request.staff:
            role = leave_request.staff.role or "Staff"
            return leave_request.staff.name, f"Staff ({role})"
        return "User", "User"

    def get(self, request):
        try:
            # Fixed window: last 24 hours
            end_dt = timezone.now()
            start_dt = end_dt - timedelta(hours=24)
            start_date = start_dt.date()
            end_date = end_dt.date()
            
            activities = []
            
            # ==========================================
            # 1. TEACHER SELF ATTENDANCE
            # ==========================================
            try:
                teacher_attendance = TeacherAttendance.objects.filter(
                    date__gte=start_date,
                    date__lte=end_date
                ).select_related('teacher')
                
                for att in teacher_attendance:
                    activities.append({
                        "date": att.date.isoformat(),
                        "what_do": f"Marked {att.status} attendance",
                        "who_do": att.teacher.name,
                        "role": "Teacher",
                        "title": f"{att.teacher.name} marked {att.status}",
                        "category": "attendance",
                        "status": att.status,
                        "message": f"{att.teacher.name} marked {att.status} attendance",
                        "timestamp": att.date.isoformat()
                    })
            except Exception as e:
                print(f"Error fetching teacher attendance: {e}")
            
            # ==========================================
            # 2. STAFF SELF ATTENDANCE
            # ==========================================
            try:
                staff_attendance = StaffAttendance.objects.filter(
                    date__gte=start_date,
                    date__lte=end_date
                ).select_related('staff')
                
                for att in staff_attendance:
                    activities.append({
                        "date": att.date.isoformat(),
                        "what_do": f"Marked {att.status} attendance",
                        "who_do": att.staff.name,
                        "role": f"Staff ({att.staff.role})",
                        "title": f"{att.staff.name} marked {att.status}",
                        "category": "attendance",
                        "status": att.status,
                        "message": f"{att.staff.name} marked {att.status} attendance",
                        "timestamp": att.date.isoformat()
                    })
            except Exception as e:
                print(f"Error fetching staff attendance: {e}")
            
            # ==========================================
            # 3. CLASS ATTENDANCE MARKED (Students)
            # ==========================================
            try:
                class_attendance = Attendance.objects.filter(
                    date__gte=start_date,
                    date__lte=end_date
                ).select_related('enrollment__student', 'enrollment__section')
                
                # Group by section and date
                attendance_by_section = defaultdict(lambda: {
                    'count': 0,
                    'section': None,
                    'date': None
                })
                
                for att in class_attendance:
                    key = f"{att.enrollment.section.id}_{att.date}"
                    attendance_by_section[key]['count'] += 1
                    attendance_by_section[key]['section'] = att.enrollment.section
                    attendance_by_section[key]['date'] = att.date
                
                for key, data in attendance_by_section.items():
                    section = data['section']
                    # Try to find class teacher
                    class_teacher = None
                    try:
                        active_year = AcademicYear.objects.get(is_current=True)
                        ct = ClassTeacher.objects.filter(
                            section=section,
                            academic_year=active_year
                        ).first()
                        if ct:
                            class_teacher = ct.teacher.name
                    except:
                        pass
                    
                    teacher_name = class_teacher or "Class Teacher"
                    
                    activities.append({
                        "date": data['date'].isoformat(),
                        "what_do": f"Marked {data['count']} student attendance",
                        "who_do": teacher_name,
                        "role": "Teacher",
                        "title": f"Attendance marked for {section.standard.name}-{section.name}: {data['count']} students",
                        "category": "student_attendance",
                        "count": data['count'],
                        "message": f"{teacher_name} marked attendance for {data['count']} students in Class {section.standard.name}-{section.name}",
                        "timestamp": data['date'].isoformat()
                    })
            except Exception as e:
                print(f"Error fetching class attendance: {e}")
            
            # ==========================================
            # 4. TEACHER SUBSTITUTIONS
            # ==========================================
            try:
                substitutions = Substitution.objects.filter(
                    date__gte=start_date,
                    date__lte=end_date
                ).select_related('slot__section', 'substitute_teacher', 'subject')
                
                for sub in substitutions:
                    activities.append({
                        "date": sub.date.isoformat(),
                        "what_do": "Substitution",
                        "who_do": sub.substitute_teacher.name,
                        "role": "Teacher",
                        "title": f"Substituted for {sub.subject.name} in {sub.slot.section.standard.name}-{sub.slot.section.name} (Period {sub.slot.period_no})",
                        "category": "substitution",
                        "description": f"Period {sub.slot.period_no}: {sub.subject.name}",
                        "message": f"{sub.substitute_teacher.name} substituted for {sub.subject.name} in Class {sub.slot.section.standard.name}-{sub.slot.section.name}, Period {sub.slot.period_no}",
                        "timestamp": sub.date.isoformat()
                    })
            except Exception as e:
                print(f"Error fetching substitutions: {e}")
            
            # ==========================================
            # 5. STAFF WORK UPDATES
            # ==========================================
            try:
                # New/pending assignments in the 24-hour window
                new_assignments = WorkAssignment.objects.filter(
                    work_order__created_date__gte=start_date,
                    work_order__created_date__lte=end_date
                ).select_related('staff', 'work_order')

                for work in new_assignments:
                    staff_name = work.staff.name if work.staff else "Staff"
                    staff_role = work.staff.role if work.staff and work.staff.role else "staff"
                    task_short = self._short_text(work.work_order.description, limit=100) or "Task"
                    task_title = self._short_text(work.work_order.description, limit=50) or "Task"

                    activities.append({
                        "date": work.work_order.created_date.isoformat(),
                        "what_do": "Assigned task",
                        "who_do": staff_name,
                        "role": f"Staff ({staff_role})",
                        "title": f"Assigned: {task_title}",
                        "category": "staff_work",
                        "status": work.status,
                        "message": f"{staff_name} received task: {task_short}",
                        "timestamp": work.work_order.created_date.isoformat()
                    })

                # Staff completing tasks
                work_completions = WorkAssignment.objects.filter(
                    completed_at__date__gte=start_date,
                    completed_at__date__lte=end_date,
                    status='Completed'
                ).select_related('staff', 'work_order')
                
                for work in work_completions:
                    staff_name = work.staff.name if work.staff else "Staff"
                    staff_role = work.staff.role if work.staff and work.staff.role else "staff"
                    task_short = self._short_text(work.work_order.description, limit=100) or "Task"
                    task_title = self._short_text(work.work_order.description, limit=50) or "Task"
                    completion_note = self._short_text(work.completion_note, limit=80)
                    done_at = work.completed_at
                    message = f"{staff_name} completed task: {task_short}"
                    if completion_note:
                        message = f"{message} (note: {completion_note})"

                    activities.append({
                        "date": done_at.date().isoformat() if done_at else work.work_order.created_date.isoformat(),
                        "what_do": "Completed task",
                        "who_do": staff_name,
                        "role": f"Staff ({staff_role})",
                        "title": f"Completed: {task_title}",
                        "category": "staff_work",
                        "status": "Completed",
                        "message": message,
                        "timestamp": done_at.isoformat() if done_at else work.work_order.created_date.isoformat()
                    })
                
                # Show staff work activity only when status is updated.
            except Exception as e:
                print(f"Error fetching staff work: {e}")
            
            # ==========================================
            # 6. LEAVE REQUESTS
            # ==========================================
            try:
                leave_requests = LeaveRequest.objects.filter(
                    created_at__gte=start_dt,
                    created_at__lte=end_dt
                ).select_related('student', 'teacher', 'staff')

                for leave in leave_requests:
                    requester_name, requester_role = self._leave_requester(leave)
                    reason_short = self._short_text(leave.reason, limit=90)
                    activities.append({
                        "date": leave.created_at.date().isoformat(),
                        "what_do": f"Submitted leave ({leave.status})",
                        "who_do": requester_name,
                        "role": requester_role,
                        "title": f"Leave request: {requester_name} ({leave.status})",
                        "category": "leave",
                        "status": leave.status,
                        "message": f"{requester_name} requested leave from {leave.start_date} to {leave.end_date}" + (f" ({reason_short})" if reason_short else ""),
                        "timestamp": leave.created_at.isoformat()
                    })
            except Exception as e:
                print(f"Error fetching leave requests: {e}")

            # ==========================================
            # 7. INVENTORY UPDATES
            # ==========================================
            try:
                stock_logs = StockLog.objects.filter(
                    timestamp__gte=start_dt,
                    timestamp__lte=end_dt
                ).select_related('item', 'staff')

                for log in stock_logs:
                    actor = log.staff.name if log.staff else "Admin"
                    actor_role = f"Staff ({log.staff.role})" if log.staff and log.staff.role else "Admin"
                    action_label = log.get_action_display()
                    qty = abs(log.quantity_changed or 0)
                    item_name = getattr(log.item, "stock_name", "Inventory item")

                    activities.append({
                        "date": log.timestamp.date().isoformat(),
                        "what_do": action_label,
                        "who_do": actor,
                        "role": actor_role,
                        "title": f"{action_label}: {item_name} ({qty})",
                        "category": "inventory",
                        "action": log.action,
                        "quantity": qty,
                        "item": item_name,
                        "message": f"{actor} {action_label.lower()} {qty} of {item_name}",
                        "timestamp": log.timestamp.isoformat()
                    })
            except Exception as e:
                print(f"Error fetching inventory updates: {e}")

            # ==========================================
            # 8. ANNOUNCEMENTS
            # ==========================================
            try:
                student_announcements = Announcement.objects.filter(
                    created_at__gte=start_dt,
                    created_at__lte=end_dt
                ).select_related('teacher', 'section', 'section__standard', 'subject')

                for ann in student_announcements:
                    desc = self._short_text(ann.description, limit=100)
                    target = f"{ann.section.standard.name}-{ann.section.name}"
                    activities.append({
                        "date": ann.created_at.date().isoformat(),
                        "what_do": f"Posted {ann.announcement_type} announcement",
                        "who_do": ann.teacher.name,
                        "role": "Teacher",
                        "title": f"Teacher announcement for Class {target}",
                        "category": "announcement",
                        "type": ann.announcement_type,
                        "target": target,
                        "subject": ann.subject.name if ann.subject else None,
                        "message": f"{ann.teacher.name} posted announcement for Class {target}: {desc}",
                        "timestamp": ann.created_at.isoformat()
                    })

                staff_announcements = StaffAnnouncement.objects.filter(
                    created_at__gte=start_dt,
                    created_at__lte=end_dt
                )
                for ann in staff_announcements:
                    desc = self._short_text(ann.description, limit=100)
                    role_target = ann.target_role or "All Staff"
                    activities.append({
                        "date": ann.created_at.date().isoformat(),
                        "what_do": "Posted staff announcement",
                        "who_do": "Admin",
                        "role": "Admin",
                        "title": f"Staff announcement: {self._short_text(ann.title, limit=60)}",
                        "category": "announcement",
                        "type": "staff",
                        "target": role_target,
                        "message": f"Admin posted staff announcement ({role_target}): {desc or ann.title}",
                        "timestamp": ann.created_at.isoformat()
                    })

                teacher_announcements = TeacherAnnouncement.objects.filter(
                    created_at__gte=start_dt,
                    created_at__lte=end_dt
                )
                for ann in teacher_announcements:
                    desc = self._short_text(ann.description, limit=100)
                    role_target = ann.target_role or "All Teachers"
                    activities.append({
                        "date": ann.created_at.date().isoformat(),
                        "what_do": "Posted teacher announcement",
                        "who_do": "Admin",
                        "role": "Admin",
                        "title": f"Teacher announcement: {self._short_text(ann.title, limit=60)}",
                        "category": "announcement",
                        "type": "teacher",
                        "target": role_target,
                        "message": f"Admin posted teacher announcement ({role_target}): {desc or ann.title}",
                        "timestamp": ann.created_at.isoformat()
                    })

                common_announcements = CommonAnnouncement.objects.filter(
                    created_at__gte=start_dt,
                    created_at__lte=end_dt
                )
                for ann in common_announcements:
                    desc = self._short_text(ann.description, limit=100)
                    activities.append({
                        "date": ann.created_at.date().isoformat(),
                        "what_do": "Posted common announcement",
                        "who_do": "Admin",
                        "role": "Admin",
                        "title": f"Common announcement: {self._short_text(ann.title, limit=60)}",
                        "category": "announcement",
                        "type": "common",
                        "target": "All",
                        "message": f"Admin posted common announcement: {desc or ann.title}",
                        "timestamp": ann.created_at.isoformat()
                    })
            except Exception as e:
                print(f"Error fetching announcements: {e}")

            # ==========================================
            # 9. EXAM MARKS UPLOADED
            # ==========================================
            try:
                field_names = {f.name for f in StudentMark._meta.fields}
                if 'created_at' in field_names:
                    marks_upload_summary = defaultdict(lambda: {
                        'count': 0,
                        'subject_name': None,
                        'exam_name': None,
                        'term_name': None,
                        'class_name': None,
                        'section_name': None,
                        'total_marks_sum': 0,
                        'obtained_marks_sum': 0,
                        'latest_ts': None
                    })

                    marks_query = StudentMark.objects.filter(
                        enrollment__academic_year__is_current=True,
                        created_at__gte=start_dt,
                        created_at__lte=end_dt
                    ).select_related(
                        'enrollment__section',
                        'enrollment__section__standard',
                        'subject',
                        'exam_type',
                        'exam_type__term'
                    )

                    for mark in marks_query:
                        group_key = f"{mark.subject.id}_{mark.exam_type.id}_{mark.enrollment.section.standard.id}_{mark.enrollment.section.id}"
                        marks_upload_summary[group_key]['count'] += 1
                        marks_upload_summary[group_key]['subject_name'] = mark.subject.name
                        marks_upload_summary[group_key]['exam_name'] = mark.exam_type.name
                        marks_upload_summary[group_key]['term_name'] = mark.exam_type.term.name
                        marks_upload_summary[group_key]['class_name'] = mark.enrollment.section.standard.name
                        marks_upload_summary[group_key]['section_name'] = mark.enrollment.section.name
                        marks_upload_summary[group_key]['total_marks_sum'] += float(mark.total_marks)
                        marks_upload_summary[group_key]['obtained_marks_sum'] += float(mark.marks_obtained)

                        current_latest = marks_upload_summary[group_key]['latest_ts']
                        if current_latest is None or mark.created_at > current_latest:
                            marks_upload_summary[group_key]['latest_ts'] = mark.created_at

                    for group_data in marks_upload_summary.values():
                        if group_data['count'] <= 0 or not group_data['latest_ts']:
                            continue

                        class_section = f"{group_data['class_name']}-{group_data['section_name']}"
                        avg_percentage = 0
                        if group_data['total_marks_sum'] > 0:
                            avg_percentage = round((group_data['obtained_marks_sum'] / group_data['total_marks_sum']) * 100, 1)

                        latest_ts = group_data['latest_ts']
                        activities.append({
                            "date": latest_ts.date().isoformat(),
                            "what_do": "Uploaded exam marks",
                            "who_do": "Teacher",
                            "role": "Teacher",
                            "title": f"Marks uploaded: {group_data['count']} students - {group_data['subject_name']} - {group_data['exam_name']} ({group_data['term_name']}) for Class {class_section}",
                            "category": "exam",
                            "subcategory": "marks_upload",
                            "subject": group_data['subject_name'],
                            "exam": f"{group_data['exam_name']} ({group_data['term_name']})",
                            "class": group_data['class_name'],
                            "section": group_data['section_name'],
                            "students_count": group_data['count'],
                            "total_marks_sum": round(group_data['total_marks_sum'], 1),
                            "obtained_marks_sum": round(group_data['obtained_marks_sum'], 1),
                            "average_percentage": avg_percentage,
                            "message": f"{group_data['count']} marks entries added for {group_data['subject_name']} - Class {class_section} ({group_data['exam_name']})",
                            "timestamp": latest_ts.isoformat()
                        })
                else:
                    recent_schedules = ExamSchedule.objects.filter(
                        created_at__gte=start_dt,
                        created_at__lte=end_dt
                    ).select_related('exam_type', 'exam_type__term')

                    if recent_schedules.exists():
                        marks_upload_summary = defaultdict(lambda: {
                            'count': 0,
                            'subject_name': None,
                            'exam_name': None,
                            'term_name': None,
                            'class_name': None,
                            'section_name': None,
                            'total_marks_sum': 0,
                            'obtained_marks_sum': 0,
                            'latest_ts': None
                        })

                        marks_query = StudentMark.objects.filter(
                            enrollment__academic_year__is_current=True,
                            schedule__in=recent_schedules
                        ).select_related(
                            'enrollment__section',
                            'enrollment__section__standard',
                            'subject',
                            'schedule__exam_type',
                            'schedule__exam_type__term'
                        )

                        for mark in marks_query:
                            section = mark.enrollment.section
                            if not section:
                                continue

                            exam_type = mark.schedule.exam_type
                            term = exam_type.term if exam_type else None
                            group_key = f"{mark.subject.id}_{mark.schedule.id}_{section.standard.id}_{section.id}"
                            marks_upload_summary[group_key]['count'] += 1
                            marks_upload_summary[group_key]['subject_name'] = mark.subject.name
                            marks_upload_summary[group_key]['exam_name'] = exam_type.name if exam_type else "Exam"
                            marks_upload_summary[group_key]['term_name'] = term.name if term else "Term"
                            marks_upload_summary[group_key]['class_name'] = section.standard.name
                            marks_upload_summary[group_key]['section_name'] = section.name
                            marks_upload_summary[group_key]['total_marks_sum'] += float(mark.total_marks)
                            marks_upload_summary[group_key]['obtained_marks_sum'] += float(mark.marks_obtained)

                            schedule_ts = mark.schedule.created_at
                            current_latest = marks_upload_summary[group_key]['latest_ts']
                            if current_latest is None or schedule_ts > current_latest:
                                marks_upload_summary[group_key]['latest_ts'] = schedule_ts

                        for group_data in marks_upload_summary.values():
                            if group_data['count'] <= 0 or not group_data['latest_ts']:
                                continue

                            class_section = f"{group_data['class_name']}-{group_data['section_name']}"
                            avg_percentage = 0
                            if group_data['total_marks_sum'] > 0:
                                avg_percentage = round((group_data['obtained_marks_sum'] / group_data['total_marks_sum']) * 100, 1)

                            latest_ts = group_data['latest_ts']
                            activities.append({
                                "date": latest_ts.date().isoformat(),
                                "what_do": "Uploaded exam marks",
                                "who_do": "Teacher",
                                "role": "Teacher",
                                "title": f"Marks uploaded: {group_data['count']} students - {group_data['subject_name']} - {group_data['exam_name']} ({group_data['term_name']}) for Class {class_section}",
                                "category": "exam",
                                "subcategory": "marks_upload",
                                "subject": group_data['subject_name'],
                                "exam": f"{group_data['exam_name']} ({group_data['term_name']})",
                                "class": group_data['class_name'],
                                "section": group_data['section_name'],
                                "students_count": group_data['count'],
                                "total_marks_sum": round(group_data['total_marks_sum'], 1),
                                "obtained_marks_sum": round(group_data['obtained_marks_sum'], 1),
                                "average_percentage": avg_percentage,
                                "message": f"{group_data['count']} marks entries added for {group_data['subject_name']} - Class {class_section} ({group_data['exam_name']})",
                                "timestamp": latest_ts.isoformat()
                            })

            except Exception as e:
                print(f"Error fetching exam marks: {e}")

            # ==========================================
            # 10. MARK CHANGE REQUESTS
            # ==========================================
            try:
                mark_change_requests = MarkChangeRequest.objects.filter(
                    created_at__gte=start_dt,
                    created_at__lte=end_dt
                ).select_related(
                    'requested_by',
                    'enrollment__student',
                    'subject',
                    'schedule__exam_type',
                    'schedule__exam_type__term'
                )

                for req in mark_change_requests:
                    teacher_name = req.requested_by.name if req.requested_by else "Teacher"
                    student_name = req.enrollment.student.student_name if req.enrollment and req.enrollment.student else "Student"
                    exam_type_name = req.schedule.exam_type.name if req.schedule and req.schedule.exam_type else "Exam"
                    term_name = req.schedule.exam_type.term.name if req.schedule and req.schedule.exam_type and req.schedule.exam_type.term else ""
                    exam_label = f"{exam_type_name} ({term_name})" if term_name else exam_type_name

                    activities.append({
                        "date": req.created_at.date().isoformat(),
                        "what_do": "Submitted mark change request",
                        "who_do": teacher_name,
                        "role": "Teacher",
                        "title": f"Mark change request for {student_name} - {req.subject.name}",
                        "category": "exam",
                        "subcategory": "mark_change_request",
                        "status": req.status,
                        "subject": req.subject.name,
                        "exam": exam_label,
                        "message": f"{teacher_name} requested mark change for {student_name} in {req.subject.name}: {req.old_marks} to {req.new_marks}",
                        "timestamp": req.created_at.isoformat()
                    })
            except Exception as e:
                print(f"Error fetching mark change requests: {e}")

            # ==========================================
            # 11. TRANSPORT UPDATES
            # ==========================================
            try:
                transport_attendance = TransportAttendance.objects.filter(
                    updated_at__gte=start_dt,
                    updated_at__lte=end_dt
                ).select_related(
                    'allocation__vehicle',
                    'allocation__student',
                    'allocation__teacher',
                    'allocation__staff',
                    'marked_by'
                )

                for rec in transport_attendance:
                    passenger_name, passenger_role = self._transport_passenger_name(rec.allocation)
                    marked_by_name = rec.marked_by.name if rec.marked_by else "Transport Staff"
                    bus_number = rec.allocation.vehicle.bus_number
                    event_ts = rec.updated_at or rec.created_at

                    activities.append({
                        "date": event_ts.date().isoformat() if event_ts else rec.date.isoformat(),
                        "what_do": "Posted bus attendance",
                        "who_do": marked_by_name,
                        "role": "Transport Staff",
                        "title": f"{marked_by_name} marked {rec.status} for {passenger_name} ({rec.trip_type}) - {bus_number}",
                        "category": "transport",
                        "status": rec.status,
                        "trip_type": rec.trip_type,
                        "bus_number": bus_number,
                        "passenger": passenger_name,
                        "passenger_role": passenger_role,
                        "message": f"{marked_by_name} posted {rec.trip_type.lower()} bus attendance as {rec.status} for {passenger_name} ({passenger_role}) on {bus_number}",
                        "timestamp": event_ts.isoformat() if event_ts else rec.date.isoformat()
                    })

                transport_expenses = TransportExpenseProof.objects.filter(
                    timestamp__gte=start_dt,
                    timestamp__lte=end_dt
                ).select_related('uploader', 'vehicle')

                for exp in transport_expenses:
                    exp_desc = self._short_text(exp.description, limit=100)
                    activities.append({
                        "date": exp.timestamp.date().isoformat(),
                        "what_do": "Uploaded transport expense",
                        "who_do": exp.uploader.name,
                        "role": "Transport Staff",
                        "title": f"Transport expense uploaded for {exp.vehicle.bus_number}: {self._short_text(exp.title, limit=50)}",
                        "category": "transport",
                        "bus_number": exp.vehicle.bus_number,
                        "message": f"{exp.uploader.name} uploaded transport expense for {exp.vehicle.bus_number}: {exp_desc or exp.title}",
                        "timestamp": exp.timestamp.isoformat()
                    })
            except Exception as e:
                print(f"Error fetching transport updates: {e}")
            
            # ==========================================
            # SORT AND FORMAT RESPONSE
            # ==========================================
            
            # Ensure each item has a message (frontend-safe)
            for act in activities:
                if not act.get("message"):
                    who = act.get("who_do", "System")
                    what = act.get("what_do") or act.get("title") or "updated data"
                    act["message"] = f"{who} {what}"

            # Sort activities by timestamp (most recent first)
            activities.sort(key=lambda x: x.get('timestamp', x['date']), reverse=True)

            # Limit to most recent 100 if there are too many
            if len(activities) > 100:
                activities = activities[:100]
            
            # Calculate summary counts
            summary = {
                "total_activities": len(activities),
                "categories": defaultdict(int),
                "roles": defaultdict(int)
            }
            
            for act in activities:
                summary["categories"][act["category"]] += 1
                summary["roles"][act["role"]] += 1
            
            # Convert defaultdict to dict for JSON serialization
            summary["categories"] = dict(summary["categories"])
            summary["roles"] = dict(summary["roles"])
            
            return Response({
                "status": 200,
                "message": "Recent activities retrieved successfully",
                "date_range": {
                    "from": start_dt.isoformat(),
                    "to": end_dt.isoformat()
                },
                "summary": summary,
                "activities": activities
            })
            
        except Exception as e:
            return Response({
                "status": 500,
                "error": "An unexpected error occurred",
                "message": str(e)
            }, status=500)
        

class AdminActivitySummaryView(APIView):
    """
    GET: Get summary counts of activities for dashboard widgets.
    """
    permission_classes = [IsAuthenticated, IsAdmin]
    
    def get(self, request):
        try:
            end_dt = timezone.now()
            start_dt = end_dt - timedelta(hours=24)
            start_date = start_dt.date()
            end_date = end_dt.date()
            teacher_ann_count = 0
            staff_ann_count = 0
            teacher_admin_ann_count = 0
            common_ann_count = 0
            transport_updates = 0
            
            # ==========================================
            # 1. TEACHER ATTENDANCE - Fixed
            # ==========================================
            try:
                teacher_attendance_count = TeacherAttendance.objects.filter(
                    date__gte=start_date,
                    date__lte=end_date
                ).count()
            except Exception as e:
                print(f"Error in teacher attendance: {e}")
                teacher_attendance_count = 0
            
            # ==========================================
            # 2. STAFF ATTENDANCE - Fixed
            # ==========================================
            try:
                staff_attendance_count = StaffAttendance.objects.filter(
                    date__gte=start_date,
                    date__lte=end_date
                ).count()
            except Exception as e:
                print(f"Error in staff attendance: {e}")
                staff_attendance_count = 0
            
            # ==========================================
            # 3. STUDENT ATTENDANCE
            # ==========================================
            try:
                student_attendance_count = Attendance.objects.filter(
                    date__gte=start_date,
                    date__lte=end_date
                ).count()
                
                # Group student attendance by section
                student_attendance_sections = Attendance.objects.filter(
                    date__gte=start_date,
                    date__lte=end_date
                ).values(
                    'enrollment__section__standard__name', 
                    'enrollment__section__name'
                ).annotate(
                    count=Count('id')
                ).order_by('-count')[:5]
                
                student_attendance_details = [
                    f"{item['enrollment__section__standard__name']}-{item['enrollment__section__name']}: {item['count']}"
                    for item in student_attendance_sections
                ]
            except Exception as e:
                print(f"Error in student attendance: {e}")
                student_attendance_count = 0
                student_attendance_details = []
            
            # ==========================================
            # 4. SUBSTITUTIONS
            # ==========================================
            try:
                substitutions_count = Substitution.objects.filter(
                    date__gte=start_date,
                    date__lte=end_date
                ).count()
            except Exception as e:
                print(f"Error in substitutions: {e}")
                substitutions_count = 0
            
            # ==========================================
            # 5. STAFF TASKS
            # ==========================================
            try:
                staff_tasks_completed = WorkAssignment.objects.filter(
                    completed_at__gte=start_dt,
                    completed_at__lte=end_dt,
                    status='Completed'
                ).count()
                
                staff_tasks_pending = WorkAssignment.objects.filter(
                    work_order__created_date__gte=start_date,
                    work_order__created_date__lte=end_date,
                    status='Pending'
                ).count()
            except Exception as e:
                print(f"Error in staff tasks: {e}")
                staff_tasks_completed = 0
                staff_tasks_pending = 0
            
            # ==========================================
            # 6. LEAVE REQUESTS
            # ==========================================
            try:
                leave_pending = LeaveRequest.objects.filter(
                    created_at__gte=start_dt,
                    created_at__lte=end_dt,
                    status='Pending'
                ).count()
                
                leave_approved = LeaveRequest.objects.filter(
                    created_at__gte=start_dt,
                    created_at__lte=end_dt,
                    status='Approved'
                ).count()
                
                leave_rejected = LeaveRequest.objects.filter(
                    created_at__gte=start_dt,
                    created_at__lte=end_dt,
                    status='Rejected'
                ).count()
            except Exception as e:
                print(f"Error in leave requests: {e}")
                leave_pending = 0
                leave_approved = 0
                leave_rejected = 0
            
            # ==========================================
            # 7. INVENTORY UPDATES
            # ==========================================
            try:
                inventory_updates = StockLog.objects.filter(
                    timestamp__gte=start_dt,
                    timestamp__lte=end_dt
                ).count()
            except Exception as e:
                print(f"Error in inventory: {e}")
                inventory_updates = 0

            # ==========================================
            # 7B. TRANSPORT UPDATES
            # ==========================================
            try:
                transport_attendance_count = TransportAttendance.objects.filter(
                    updated_at__gte=start_dt,
                    updated_at__lte=end_dt
                ).count()
                transport_expense_count = TransportExpenseProof.objects.filter(
                    timestamp__gte=start_dt,
                    timestamp__lte=end_dt
                ).count()
                transport_updates = transport_attendance_count + transport_expense_count
            except Exception as e:
                print(f"Error in transport updates: {e}")
                transport_updates = 0
            
            # ==========================================
            # 8. ANNOUNCEMENTS
            # ==========================================
            try:
                teacher_ann_count = Announcement.objects.filter(
                    created_at__gte=start_dt,
                    created_at__lte=end_dt
                ).count()
                staff_ann_count = StaffAnnouncement.objects.filter(
                    created_at__gte=start_dt,
                    created_at__lte=end_dt
                ).count()
                teacher_admin_ann_count = TeacherAnnouncement.objects.filter(
                    created_at__gte=start_dt,
                    created_at__lte=end_dt
                ).count()
                common_ann_count = CommonAnnouncement.objects.filter(
                    created_at__gte=start_dt,
                    created_at__lte=end_dt
                ).count()
                
                total_announcements = teacher_ann_count + staff_ann_count + teacher_admin_ann_count + common_ann_count
            except Exception as e:
                print(f"Error in announcements: {e}")
                total_announcements = 0
            
            # ==========================================
            # 9. EXAM MARKS - Fixed (Count with proper date filter)
            # ==========================================
            try:
                field_names = {f.name for f in StudentMark._meta.fields}
                if 'created_at' in field_names:
                    marks_uploaded = StudentMark.objects.filter(
                        enrollment__academic_year__is_current=True,
                        created_at__gte=start_dt,
                        created_at__lte=end_dt
                    ).count()
                    marks_details = f"{marks_uploaded} student mark{'s' if marks_uploaded != 1 else ''} uploaded in last 24 hours"
                else:
                    marks_uploaded = 0
                    marks_details = "StudentMark upload timestamp is not available"
            except Exception as e:
                print(f"Error in exam marks: {e}")
                marks_uploaded = 0
                marks_details = "Marks upload data unavailable"
            
            # ==========================================
            # 10. MARK CHANGE REQUESTS
            # ==========================================
            try:
                mark_change_pending = MarkChangeRequest.objects.filter(
                    created_at__gte=start_dt,
                    created_at__lte=end_dt,
                    status='PENDING'
                ).count()
            except Exception as e:
                print(f"Error in mark change requests: {e}")
                mark_change_pending = 0
            
            # ==========================================
            # BUILD RESPONSE
            # ==========================================
            
            summary = {
                "today": {
                    "teacher_attendance": {
                        "count": teacher_attendance_count,
                        "details": f"{teacher_attendance_count} teacher{'s' if teacher_attendance_count != 1 else ''} marked attendance",
                        "icon": "👨‍🏫"
                    },
                    "staff_attendance": {
                        "count": staff_attendance_count,
                        "details": f"{staff_attendance_count} staff member{'s' if staff_attendance_count != 1 else ''} marked attendance",
                        "icon": "👔"
                    },
                    "student_attendance": {
                        "count": student_attendance_count,
                        "details": f"{student_attendance_count} student{'s' if student_attendance_count != 1 else ''} marked present",
                        "section_wise": student_attendance_details[:5],
                        "icon": "👥"
                    },
                    "substitutions": {
                        "count": substitutions_count,
                        "details": f"{substitutions_count} substitution{'s' if substitutions_count != 1 else ''} arranged",
                        "icon": "🔄"
                    },
                    "staff_tasks": {
                        "completed": staff_tasks_completed,
                        "pending": staff_tasks_pending,
                        "total": staff_tasks_completed + staff_tasks_pending,
                        "details": f"{staff_tasks_completed} completed, {staff_tasks_pending} pending",
                        "icon": "✅"
                    },
                    "leave_requests": {
                        "pending": leave_pending,
                        "approved": leave_approved,
                        "rejected": leave_rejected,
                        "total": leave_pending + leave_approved + leave_rejected,
                        "details": f"{leave_pending} pending, {leave_approved} approved",
                        "icon": "📝"
                    },
                    "inventory_updates": {
                        "count": inventory_updates + transport_updates,
                        "details": f"{inventory_updates} inventory and {transport_updates} transport transaction{'s' if transport_updates != 1 else ''}",
                        "icon": "📦"
                    },
                    "announcements": {
                        "count": total_announcements,
                        "details": f"{total_announcements} announcement{'s' if total_announcements != 1 else ''} posted",
                        "breakdown": {
                            "teacher_to_student": teacher_ann_count,
                            "admin_to_staff": staff_ann_count,
                            "admin_to_teacher": teacher_admin_ann_count,
                            "admin_to_all": common_ann_count
                        },
                        "icon": "📢"
                    },
                    "exam_marks": {
                        "count": marks_uploaded,
                        "details": marks_details,
                        "icon": "📊"
                    },
                    "mark_change_requests": {
                        "pending": mark_change_pending,
                        "details": f"{mark_change_pending} pending approval{'s' if mark_change_pending != 1 else ''}",
                        "icon": "✏️"
                    }
                }
            }
            
            # Calculate overall summary
            total_activities = (
                teacher_attendance_count +
                staff_attendance_count +
                student_attendance_count +
                substitutions_count +
                staff_tasks_completed +
                leave_pending + leave_approved +
                inventory_updates + transport_updates +
                total_announcements +
                marks_uploaded +
                mark_change_pending
            )
            
            summary["today"]["overall"] = {
                "total_activities": total_activities,
                "details": f"{total_activities} total activities in last 24 hours",
                "icon": "📋"
            }
            
            return Response({
                "status": 200,
                "date_range": {
                    "from": start_dt.isoformat(),
                    "to": end_dt.isoformat()
                },
                "data": summary
            })
            
        except Exception as e:
            import traceback
            print(f"Critical error in AdminActivitySummaryView: {str(e)}")
            print(traceback.format_exc())
            
            return Response({
                "status": 500,
                "error": "An unexpected error occurred while fetching activity summary",
                "message": str(e)
            }, status=500)


class AdminInventoryUpdatesView(APIView):
    """
    GET: Inventory updates for admin dashboard.
    Query Params:
        - no params: last 24 hours
        - filter: today | this_week | past_week | this_month | past_month
        - date: YYYY-MM-DD (optional, enables date-based filtering)
        - days: number of days to look back from `date` (optional, defaults to 1)
        - staff_type: optional inventory staff type filter
    """
    permission_classes = [IsAuthenticated, IsAdmin]

    def get(self, request):
        try:
            date_param = request.query_params.get('date')
            days_raw = request.query_params.get('days')
            staff_type = request.query_params.get('staff_type')
            filter_param = (request.query_params.get('filter') or request.query_params.get('period') or "").strip().lower()
            has_days_filter = days_raw is not None
            has_filter = bool(filter_param)
            use_last_24_hours = not date_param and not has_days_filter
            use_datetime_window = False

            if has_filter and (date_param or has_days_filter):
                return Response({
                    "status": 400,
                    "error": "Use either 'filter' OR 'date/days', not both"
                }, status=400)

            if has_filter:
                valid_filters = {"today", "this_week", "past_week", "this_month", "past_month"}
                if filter_param not in valid_filters:
                    return Response({
                        "status": 400,
                        "error": "Invalid filter. Use: today, this_week, past_week, this_month, past_month"
                    }, status=400)

                now = timezone.now()
                tz = timezone.get_current_timezone()

                if filter_param == "today":
                    start_dt = now.replace(hour=0, minute=0, second=0, microsecond=0)
                    end_dt = now
                elif filter_param == "this_week":
                    week_start_date = now.date() - timedelta(days=now.weekday())
                    start_dt = timezone.make_aware(datetime.combine(week_start_date, datetime.min.time()), tz)
                    end_dt = now
                elif filter_param == "past_week":
                    this_week_start = now.date() - timedelta(days=now.weekday())
                    past_week_start = this_week_start - timedelta(days=7)
                    past_week_end = this_week_start - timedelta(days=1)
                    start_dt = timezone.make_aware(datetime.combine(past_week_start, datetime.min.time()), tz)
                    end_dt = timezone.make_aware(datetime.combine(past_week_end, datetime.max.time()), tz)
                elif filter_param == "this_month":
                    month_start_date = now.date().replace(day=1)
                    start_dt = timezone.make_aware(datetime.combine(month_start_date, datetime.min.time()), tz)
                    end_dt = now
                else:  # past_month
                    this_month_start = now.date().replace(day=1)
                    past_month_end = this_month_start - timedelta(days=1)
                    past_month_start = past_month_end.replace(day=1)
                    start_dt = timezone.make_aware(datetime.combine(past_month_start, datetime.min.time()), tz)
                    end_dt = timezone.make_aware(datetime.combine(past_month_end, datetime.max.time()), tz)

                use_datetime_window = True

            # Default mode: empty request -> last 24 hours
            elif use_last_24_hours:
                end_dt = timezone.now()
                start_dt = end_dt - timedelta(hours=24)
                start_date = start_dt.date()
                target_date = end_dt.date()
                use_datetime_window = True
            else:
                if not days_raw:
                    days_raw = 1
                try:
                    days_back = int(days_raw)
                except (TypeError, ValueError):
                    return Response({
                        "status": 400,
                        "error": "Invalid days value. Use a positive integer"
                    }, status=400)

                if days_back < 1:
                    return Response({
                        "status": 400,
                        "error": "Invalid days value. Use a positive integer"
                    }, status=400)

                if date_param:
                    try:
                        target_date = datetime.strptime(date_param, '%Y-%m-%d').date()
                    except ValueError:
                        return Response({
                            "status": 400,
                            "error": "Invalid date format. Use YYYY-MM-DD"
                        }, status=400)
                else:
                    target_date = timezone.now().date()

                start_date = target_date - timedelta(days=days_back - 1)
            updates = []
            summary = {
                "total_updates": 0,
                "actions": {"used": 0, "damaged": 0, "restocked": 0, "added": 0}
            }

            try:
                if use_datetime_window:
                    logs_qs = StockLog.objects.filter(
                        timestamp__gte=start_dt,
                        timestamp__lte=end_dt
                    ).select_related('item', 'staff')
                else:
                    logs_qs = StockLog.objects.filter(
                        timestamp__date__gte=start_date,
                        timestamp__date__lte=target_date
                    ).select_related('item', 'staff')

                if staff_type:
                    logs_qs = logs_qs.filter(item__staff_type=staff_type)

                for log in logs_qs:
                    staff_name = log.staff.name if log.staff else "Admin"
                    role_text = f"Staff ({log.staff.role})" if log.staff else "Admin"
                    quantity = abs(log.quantity_changed)
                    action_text = {
                        "used": "used",
                        "damaged": "marked as damaged",
                        "restocked": "restocked"
                    }.get(log.action, log.action)

                    updates.append({
                        "date": log.timestamp.date().isoformat(),
                        "category": "inventory",
                        "what_do": f"Inventory {log.action}",
                        "who_do": staff_name,
                        "role": role_text,
                        "item_id": log.item.id,
                        "item": log.item.stock_name,
                        "action": log.action,
                        "quantity": quantity,
                        "message": f"{staff_name} {action_text} {quantity} unit(s) of {log.item.stock_name}",
                        "timestamp": log.timestamp.isoformat()
                    })
                    summary["actions"][log.action] = summary["actions"].get(log.action, 0) + 1

            except Exception:
                logger.exception("Error fetching stock logs in AdminInventoryUpdatesView")

            try:
                if use_datetime_window:
                    items_qs = InventoryItem.objects.filter(
                        last_updated__gte=start_dt,
                        last_updated__lte=end_dt
                    )
                else:
                    items_qs = InventoryItem.objects.filter(
                        last_updated__date__gte=start_date,
                        last_updated__date__lte=target_date
                    )

                if staff_type:
                    items_qs = items_qs.filter(staff_type=staff_type)

                for item in items_qs.order_by('-last_updated'):
                    updates.append({
                        "date": item.last_updated.date().isoformat(),
                        "category": "inventory",
                        "what_do": "Added inventory item",
                        "who_do": "Admin",
                        "role": "Admin",
                        "item_id": item.id,
                        "item": item.stock_name,
                        "action": "added",
                        "quantity": item.initial_quantity,
                        "message": f"Admin added new inventory item {item.stock_name} ({item.initial_quantity} unit(s))",
                        "timestamp": item.last_updated.isoformat()
                    })
                    summary["actions"]["added"] += 1
            except Exception:
                logger.exception("Error fetching new inventory items in AdminInventoryUpdatesView")

            updates.sort(key=lambda x: x["timestamp"], reverse=True)
            if len(updates) > 100:
                updates = updates[:100]

            summary["total_updates"] = len(updates)

            if use_datetime_window:
                date_range = {
                    "from": start_dt.isoformat(),
                    "to": end_dt.isoformat()
                }
            else:
                date_range = {
                    "from": start_date.isoformat(),
                    "to": target_date.isoformat()
                }

            return Response({
                "status": 200,
                "message": "Inventory updates retrieved successfully",
                "date_range": date_range,
                "summary": summary,
                "updates": updates
            }, status=200)

        except Exception as e:
            logger.exception("Critical error in AdminInventoryUpdatesView")
            return Response({
                "status": 500,
                "error": "An unexpected error occurred while fetching inventory updates",
                "message": str(e)
            }, status=500)


class AdminInventoryChartView(APIView):
    """
    GET: Inventory chart metrics for admin dashboard.
    No request params required.
    Returns totals for: initial, added, restocked, used, damaged.
    """
    permission_classes = [IsAuthenticated, IsAdmin]

    def get(self, request):
        try:
            items = InventoryItem.objects.all().order_by('stock_name')
            item_wise = []
            for item in items:
                logs = StockLog.objects.filter(item=item).order_by('timestamp', 'id')
                running_qty = int(item.initial_quantity)

                logs_data = []
                for log in logs:
                    delta = int(log.quantity_changed)
                    running_qty += delta

                    row = {
                        "id": log.id,
                        "staff_name": log.staff.name if log.staff else None,
                        "staff_id": log.staff.staff_id if log.staff else None,
                        "action": log.action,
                        "timestamp": log.timestamp.isoformat() if log.timestamp else None,
                        "current_quantity": running_qty
                    }

                    # Keep restocked field explicit as requested
                    if log.action == 'restocked':
                        row["restocked_changed"] = delta
                    else:
                        row["quantity_changed"] = delta

                    logs_data.append(row)

                initial_ts = getattr(item, 'initial_set_at', None) or item.last_updated
                item_wise.append({
                    "id": item.id,
                    "stock_name": item.stock_name,
                    "staff_type": item.staff_type,
                    "initial_quantity": int(item.initial_quantity),
                    "timestamp": initial_ts.isoformat() if initial_ts else None,
                    "logs": logs_data
                })

            return Response({
                "status": 200,
                "message": "Inventory chart data retrieved successfully",
                "items": item_wise
            }, status=200)

        except Exception as e:
            logger.exception("Critical error in AdminInventoryChartView")
            return Response({
                "status": 500,
                "error": "An unexpected error occurred while fetching inventory chart data",
                "message": str(e)
            }, status=500)
        

class AdminStaffTodayWorkStatusView(APIView):
    """
    GET: Today's staff work split by Pending and Completed.
    """
    permission_classes = [IsAuthenticated, IsAdmin]

    def _serialize_assignment(self, assignment):
        proof_url = None
        if assignment.proof_file:
            try:
                proof_url = assignment.proof_file.url
            except Exception:
                proof_url = None

        staff = assignment.staff
        return {
            "assignment_id": assignment.id,
            "task_id": assignment.work_order.id if assignment.work_order else None,
            "task_description": assignment.work_order.description if assignment.work_order else "",
            "staff_id": staff.staff_id if staff else None,
            "staff_name": staff.name if staff else "Staff",
            "staff_role": staff.role if staff else None,
            "status": assignment.status,
            "date": assignment.work_order.created_date.isoformat() if assignment.work_order else None,
            "completed_at": assignment.completed_at.isoformat() if assignment.completed_at else None,
            "completion_note": assignment.completion_note or "",
            "proof_url": proof_url
        }

    def get(self, request):
        try:
            today = timezone.now().date()

            pending_qs = WorkAssignment.objects.filter(
                work_order__created_date=today,
                status='Pending'
            ).select_related('staff', 'work_order').order_by('-id')

            completed_qs = WorkAssignment.objects.filter(
                work_order__created_date=today,
                status='Completed'
            ).select_related('staff', 'work_order').order_by('-completed_at', '-id')

            pending_data = [self._serialize_assignment(item) for item in pending_qs]
            completed_data = [self._serialize_assignment(item) for item in completed_qs]

            pending_count = len(pending_data)
            completed_count = len(completed_data)
            total_count = pending_count + completed_count

            return Response({
                "status": 200,
                "date": today.isoformat(),
                "message": "Staff work status retrieved successfully" if total_count > 0 else "No staff work found for today",
                "data": {
                    "pending": {
                        "count": pending_count,
                        "works": pending_data
                    },
                    "completed": {
                        "count": completed_count,
                        "works": completed_data
                    },
                    "overall": {
                        "total": total_count,
                        "pending": pending_count,
                        "completed": completed_count
                    }
                }
            }, status=200)

        except Exception as e:
            logger.exception("Critical error in AdminStaffTodayWorkStatusView")
            return Response({
                "status": 500,
                "error": "An unexpected error occurred while fetching staff work status",
                "message": str(e)
            }, status=500)
