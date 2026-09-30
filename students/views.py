import json

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status, viewsets
from rest_framework.permissions import IsAuthenticated
from django.shortcuts import get_object_or_404
from django.db import transaction

from django.core.serializers.json import DjangoJSONEncoder

# --- MODELS ---
from .models import (
    Student,
    Enrollment,
    StudentRolePermission,
    STUDENT_PERMISSION_GROUPS,
    STUDENT_PERMISSION_LABELS,
)
from .serializers import (
    StudentProfileSerializer,
    SimpleStudentSerializer,
    EnrollmentSerializer,
    StudentRolePermissionSerializer,
)
from teachers.models import Teacher
from academics.models import Section, Standard, ClassTeacher
from school.models import AcademicYear
from subjects.models import Subject
from timetable.models import TimetableSlot  # <--- NEW: For Subject Teacher Permissions

# --- PERMISSIONS & MIXINS ---
from school.mixins import AcademicYearContextMixin 
from school.tenant import get_active_academic_year, get_requested_school, scope_queryset_for_user
from school.models import School
# (Assuming IsStudent is defined elsewhere or not strictly needed if checking user_type)

# ==========================================
# 1. STUDENT: View Own Profile
# ==========================================
class StudentRolePermissionView(APIView):
    permission_classes = [IsAuthenticated]

    def _can_admin_manage(self, request):
        return request.user.user_type in ('admin', 'super_admin')

    def _school_for_request(self, request):
        school = get_requested_school(request)
        if school:
            return school
        if request.user.user_type == 'super_admin':
            return School.objects.filter(is_active=True).first() or School.objects.first()
        return None

    def _settings_for_school(self, school):
        return StudentRolePermission.objects.get_or_create(school=school)[0]

    def get(self, request):
        if request.user.user_type == 'student':
            student = get_object_or_404(Student, user=request.user)
            if not student.school_id:
                return Response({"error": "Student is not assigned to a school."}, status=400)
            settings_obj = self._settings_for_school(student.school)
            serializer = StudentRolePermissionSerializer(settings_obj)
            return Response({"status": 200, "data": serializer.data}, status=status.HTTP_200_OK)

        if not self._can_admin_manage(request):
            return Response({"error": "Only students and admins can access role permissions."}, status=403)

        school = self._school_for_request(request)
        if not school:
            return Response({"error": "Select a school before managing student roles."}, status=400)

        settings_obj = self._settings_for_school(school)
        serializer = StudentRolePermissionSerializer(settings_obj)
        return Response({
            "status": 200,
            "permission_groups": STUDENT_PERMISSION_GROUPS,
            "permission_labels": STUDENT_PERMISSION_LABELS,
            "data": serializer.data,
        }, status=status.HTTP_200_OK)

    def put(self, request):
        if not self._can_admin_manage(request):
            return Response({"error": "Only admins can update student role permissions."}, status=403)

        school = self._school_for_request(request)
        if not school:
            return Response({"error": "Select a school before updating student roles."}, status=400)

        settings_obj = self._settings_for_school(school)
        serializer = StudentRolePermissionSerializer(settings_obj, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save()
            return Response({
                "status": 200,
                "message": f"Student role permissions updated for {school.name}.",
                "data": serializer.data,
            }, status=status.HTTP_200_OK)
        return Response(serializer.errors, status=400)


class StudentProfileView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        try:
            # Check if the user is actually a student
            student_profile = request.user.student_profile
        except AttributeError:
            return Response({"error": "Access Denied. Students only."}, status=403)
        
        serializer = StudentProfileSerializer(student_profile)
        return Response({
            "status": 200,
            "data": serializer.data
        }, status=status.HTTP_200_OK)


# ==========================================
# 2. SHARED: View Specific Student Details
# ==========================================
class StudentDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        target_id = request.query_params.get('student_id')
        if not target_id:
            return Response({"error": "student_id is required"}, status=400)

        try:
            student = scope_queryset_for_user(Student.objects.all(), request).get(student_id=target_id)
            user = request.user

            # PERMISSION CHECK: Teacher can only view their own class
            if hasattr(user, 'teacher_profile'):
                teacher = user.teacher_profile
                try:
                    active_year = AcademicYear.objects.get(is_current=True)
                except AcademicYear.DoesNotExist:
                    return Response({"error": "No Active Year"}, 500)

                # Check Class Teacher via New Model
                is_class_teacher = ClassTeacher.objects.filter(
                    teacher=teacher, 
                    section=student.section, # Assuming Sync is correct
                    academic_year=active_year
                ).exists()

                if not is_class_teacher:
                    # Optional: Allow Subject Teachers to view too? 
                    # For now, strict Class Teacher only as per your logic.
                    return Response({"error": "Access Denied: Student is not in your assigned class"}, status=403)

            serializer = StudentProfileSerializer(student)
            return Response(serializer.data, status=200)

        except Student.DoesNotExist:
            return Response({"error": "Student not found"}, status=404)


# ==========================================
# 3. SHARED: List View (Class Roster)
# ==========================================
class StudentListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        
        # A. TEACHER LOGIC (Class Teacher Only)
        if hasattr(user, 'teacher_profile'):
            try:
                active_year = AcademicYear.objects.get(is_current=True)
                teacher = user.teacher_profile
                
                # FIX: Use ClassTeacher model instead of old 'assigned_section'
                ct_record = ClassTeacher.objects.filter(
                    teacher=teacher, 
                    academic_year=active_year
                ).first()

                if not ct_record:
                    return Response({"error": "You are not a Class Teacher for the active year"}, status=400)

                # Fetch students in this section
                students = Student.objects.filter(section=ct_record.section, school=teacher.school)
                serializer = SimpleStudentSerializer(students, many=True)
                
                return Response({
                    "class_info": f"{ct_record.section.standard.name} - {ct_record.section.name}",
                    "count": students.count(),
                    "students": serializer.data
                })
            except AcademicYear.DoesNotExist:
                return Response({"error": "No Active Year"}, 500)

        # B. ADMIN LOGIC
        elif getattr(user, 'user_type', '') in ('admin', 'super_admin'):
            class_name = request.query_params.get('class')
            section_name = request.query_params.get('section')
            
            if not class_name or not section_name:
                return Response({"error": "Class and Section parameters are required for Admin"}, status=400)

            students = scope_queryset_for_user(Student.objects.all(), request).filter(
                section__standard__name=class_name, 
                section__name=section_name
            )
            serializer = SimpleStudentSerializer(students, many=True)
            return Response(serializer.data)

        return Response({"error": "Access Denied"}, status=403)


# ==========================================
# 4. ADMIN: Bulk Assign Students (THE FIX)
# ==========================================
class AdminAssignStudentsView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        if getattr(request.user, 'user_type', '') not in ('admin', 'super_admin'):
            return Response({"error": "Only Admins can perform this action"}, status=403)

        # 1. Get Active Year
        active_year = get_active_academic_year(request)
        if not active_year:
            return Response({"error": "No Active Academic Year found. Cannot enroll students."}, 500)

        data = request.data
        class_name = data.get('class')
        section_name = data.get('section')
        students_list = data.get('students') 

        if not all([class_name, section_name, students_list]):
            return Response({"error": "Missing class, section, or students list"}, status=400)

        try:
            # 2. Find Target Section
            section = scope_queryset_for_user(Section.objects.select_related("standard"), request).get(
                standard__name=class_name,
                name=section_name,
            )
            standard_obj = section.standard

            success_list = []
            failed_list = []

            # 3. Transaction for Safety
            with transaction.atomic():
                for s_data in students_list:
                    sid = s_data.get('student_id')
                    
                    try:
                        student = scope_queryset_for_user(Student.objects.all(), request).get(student_id=sid)
                        
                        # --- FIX PART A: Update "ID Card" (Student Profile) ---
                        student.section = section
                        student.standard = standard_obj
                        student.save()
                        
                        # --- FIX PART B: Sign "The Contract" (Enrollment) ---
                        # We use update_or_create to handle re-assignment within the same year safely
                        Enrollment.objects.update_or_create(
                            student=student,
                            academic_year=active_year,
                            defaults={
                                'school': student.school,
                                'standard': standard_obj,
                                'section': section,
                                'is_active': True,
                                'promoted': False
                            }
                        )
                        
                        success_list.append(sid)

                    except Student.DoesNotExist:
                        failed_list.append(sid)

            return Response({
                "message": "Assignment & Enrollment Completed",
                "academic_year": active_year.name,
                "assigned_to": f"{class_name} - {section_name}",
                "success_count": len(success_list),
                "failed_count": len(failed_list),
                "successful_ids": success_list,
                "failed_ids_not_found": failed_list
            }, status=200)

        except Section.DoesNotExist:
            return Response({"error": f"Class '{class_name}' Section '{section_name}' does not exist."}, status=404)


# ==========================================
# 5. SUBJECT TEACHER: Get Student List
# ==========================================
class SubjectTeacherStudentListView(APIView):
    """
    GET: List all students in a specific Class & Section.
    - Validates if the teacher has a TIMETABLE SLOT for this section.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        cls = request.query_params.get('class')
        sec = request.query_params.get('section')
        sub_name = request.query_params.get('subject')

        if not all([cls, sec, sub_name]):
            return Response({"error": "Params 'class', 'section', and 'subject' are required"}, 400)

        # 1. Verify Teacher & Active Year
        try:
            teacher_profile = user.teacher_profile
            active_year = AcademicYear.objects.get(is_current=True)
        except AttributeError:
            return Response({"error": "You are not a registered teacher"}, 403)
        except AcademicYear.DoesNotExist:
            return Response({"error": "No Active Year"}, 500)

        # 2. Security Check: TIMETABLE CHECK
        # "Does this teacher have a slot for this subject in this section this year?"
        has_slot = TimetableSlot.objects.filter(
            academic_year=active_year,
            teacher=teacher_profile,
            section__standard__name=cls,
            section__name=sec,
            subject__name__iexact=sub_name
        ).exists()

        if not has_slot:
            # Fallback: Check if they are Class Teacher (Class Teachers usually can access all subjects)
            is_class_teacher = ClassTeacher.objects.filter(
                academic_year=active_year,
                teacher=teacher_profile,
                section__standard__name=cls,
                section__name=sec
            ).exists()
            
            if not is_class_teacher:
                return Response({"error": f"Permission Denied: You are not scheduled to teach {sub_name} in {cls}-{sec}."}, 403)

        # 3. Fetch Students
        students = Student.objects.filter(
            section__standard__name=cls,
            section__name=sec
        ).values('student_id', 'student_name')

        if not students:
             return Response({"message": "No students found in this class"}, 404)

        return Response({
            "class": cls,
            "section": sec,
            "subject": sub_name,
            "total_students": len(students),
            "students": list(students)
        }, 200)


# ==========================================
# 6. ENROLLMENT VIEWSET
# ==========================================
class EnrollmentViewSet(AcademicYearContextMixin, viewsets.ModelViewSet):
    queryset = Enrollment.objects.all()
    serializer_class = EnrollmentSerializer
    permission_classes = [IsAuthenticated]


    # ==========================================
# 7. SUBJECT TEACHER: View Student Profile
# ==========================================
class SubjectTeacherStudentProfileView(APIView):
    """
    GET: Subject Teacher views a specific student's profile.
    Allowed ONLY if the teacher has a TimetableSlot for the student's class in the ACTIVE YEAR.
    
    Query Params: ?student_id=1001
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        target_id = request.query_params.get('student_id')
        if not target_id:
            return Response({"error": "student_id parameter is required"}, status=400)

        # 1. Find the Student
        try:
            student = Student.objects.get(student_id=target_id)
        except Student.DoesNotExist:
            return Response({"error": "Student not found"}, status=404)

        # 2. Verify Teacher & Active Year
        try:
            teacher_profile = request.user.teacher_profile
            active_year = AcademicYear.objects.get(is_current=True)
        except AttributeError:
            return Response({"error": "Access Denied. You are not a registered teacher."}, status=403)
        except AcademicYear.DoesNotExist:
            return Response({"error": "No Active Academic Year configured."}, status=500)

        # 3. Check if the student is assigned to a section
        if not student.section:
            return Response({"error": "This student is not assigned to any class/section yet."}, status=400)

        # 4. STRICT SECURITY CHECK: Does this teacher teach this student's section?
        teaches_student = TimetableSlot.objects.filter(
            academic_year=active_year,
            teacher=teacher_profile,
            section=student.section
        ).exists()

        if not teaches_student:
            return Response({"error": "Permission Denied: You do not teach any subjects for this student's class."}, status=403)

        # 5. Return the Profile Data
        serializer = StudentProfileSerializer(student)
        
        return Response({
            "status": 200,
            "data": serializer.data
        }, status=status.HTTP_200_OK)


########## siva bro code ##########
# ==========================================
# STUDENT OVERVIEW VIEW (Complete Dashboard)
# ==========================================

from django.db.models import Count, Q, Avg
from datetime import date, timedelta
from attendance.models import Attendance as StudentAttendance
from exams.models import StudentMark, ExamType, ExamTerm
from reports.models import BehaviorReport
from fees.models import StudentFee, FeePayment
from leave_management.models import LeaveRequest
from collections import defaultdict
import logging
from holidays.models import Holiday
from exams.models import ExamSchedule
from transport.models import TransportAllocation, TransportAttendance

logger = logging.getLogger(__name__)

class StudentOverviewView(APIView):
    """
    GET: Returns comprehensive overview of a student including:
    - Basic profile info
    - Academic info (class, section, enrollment)
    - Attendance summary (current year)
    - Exam performance (latest exams)
    - Behavior reports
    - Fee status
    - Leave history
    - Contact information
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        student_id = request.query_params.get('student_id')
        if not student_id:
            return Response({"error": "student_id parameter is required"}, status=400)

        active_year = get_active_academic_year(request)
        if not active_year:
            return Response({"error": "No Active Academic Year configured"}, status=500)

        user = request.user
        
        # PERMISSION CHECKS
        is_authorized = False
        
        # 1. Admin can view any student
        if getattr(user, 'user_type', '') in ('admin', 'super_admin'):
            is_authorized = True
        
        # 2. Teacher can view if they are Class Teacher or Subject Teacher
        elif hasattr(user, 'teacher_profile'):
            teacher = user.teacher_profile
            
            # Check if teacher is Class Teacher for this student
            try:
                # Get student's enrollment
                enrollment = Enrollment.objects.get(
                    student__student_id=student_id,
                    student__in=scope_queryset_for_user(Student.objects.all(), request),
                    academic_year=active_year,
                    is_active=True
                )
                
                # Check Class Teacher assignment
                is_class_teacher = ClassTeacher.objects.filter(
                    teacher=teacher,
                    section=enrollment.section,
                    academic_year=active_year
                ).exists()
                
                if is_class_teacher:
                    is_authorized = True
                else:
                    # Check if teacher teaches any subject to this student's section
                    is_subject_teacher = TimetableSlot.objects.filter(
                        academic_year=active_year,
                        teacher=teacher,
                        section=enrollment.section
                    ).exists()
                    
                    if is_subject_teacher:
                        is_authorized = True
                        
            except Enrollment.DoesNotExist:
                pass
        
        # 3. Student can only view their own profile
        elif hasattr(user, 'student_profile'):
            if user.student_profile.student_id == student_id:
                is_authorized = True
        
        # 4. Parent? (If you have parent model)
        # elif hasattr(user, 'parent_profile'):
        #     # Check if this student is linked to parent
        #     pass
        
        if not is_authorized:
            return Response({"error": "Access Denied: You are not authorized to view this student's details"}, status=403)

        try:
            # Get student with related data
            student = Student.objects.select_related(
                'section', 
                'section__standard'
            )
            student = scope_queryset_for_user(student, request).get(student_id=student_id)
            
            # Get current enrollment
            enrollment = Enrollment.objects.filter(
                student=student,
                academic_year=active_year,
                is_active=True
            ).select_related('section', 'section__standard').first()

            # If no active enrollment but student exists
            if not enrollment:
                return Response({
                    "error": f"Student {student.student_name} is not enrolled in the current academic year ({active_year.name})"
                }, status=404)

            # ====================
            # 1. BASIC PROFILE INFO
            # ====================
            profile_data = {
                "student_id": student.student_id,
                "student_name": student.student_name,
                "profile_image": student.profile_image.url if student.profile_image else None,
                "gender": student.gender,
                "date_of_birth": student.date_of_birth,
                "age": self.calculate_age(student.date_of_birth) if student.date_of_birth else None,
                "student_email": student.student_email,
                "address": student.address,
                "date_of_admission": student.date_of_admission,
                "extra_details": student.extra_details if student.extra_details else {}
            }

            # ====================
            # 2. ACADEMIC INFO
            # ====================
            academic_data = {
                "academic_year": active_year.name,
                "class": enrollment.section.standard.name if enrollment.section.standard else "Not Assigned",
                "section": enrollment.section.name if enrollment.section else "Not Assigned",
                "class_teacher": self.get_class_teacher(enrollment.section, active_year),
                "roll_number": getattr(student, 'roll_no', None),
                "enrollment_status": "Active" if enrollment.is_active else "Inactive",
                "promoted": enrollment.promoted if hasattr(enrollment, 'promoted') else False
            }

            # ====================
            # 3. PARENT/GUARDIAN INFO
            # ====================
            parent_data = {
                "father_name": student.father_name,
                "mother_name": student.mother_name,
                "father_phone": student.father_phone,
                "mother_phone": student.mother_phone,
                "emergency_contact": student.father_phone or student.mother_phone or "Not Available"
            }

            # ====================
            # 4. ATTENDANCE SUMMARY
            # ====================
            attendance_data = self.get_attendance_summary(enrollment, active_year)

            # ====================
            # 5. EXAM PERFORMANCE
            # ====================
            exam_data = self.get_exam_performance(enrollment, active_year)

            # ====================
            # 6. BEHAVIOR REPORTS
            # ====================
            behavior_data = self.get_behavior_reports(enrollment)

            # ====================
            # 7. FEE STATUS
            # ====================
            fee_data = self.get_fee_status(student, active_year)

            # ====================
            # 8. LEAVE HISTORY
            # ====================
            leave_data = self.get_leave_history(student, active_year)

            # ====================
            # 9. OVERALL STATISTICS
            # ====================
            overall_stats = self.calculate_overall_stats(
                attendance_data, 
                exam_data, 
                behavior_data, 
                fee_data
            )

            # ====================
            # 10. TRANSPORT DETAILS
            # ====================
            transport_data = self.get_transport_details(student)

            response_data = {
                "status": 200,
                "message": "Student overview retrieved successfully",
                "timestamp": date.today().isoformat(),
                "data": {
                    "profile": profile_data,
                    "academic": academic_data,
                    "parents": parent_data,
                    "today_snapshot": {
                        "date": date.today().isoformat(),
                        "today_attendance_status": attendance_data.get("today", {}).get("status", "Not Marked"),
                        "attendance": attendance_data.get("today", {})
                    },
                    "attendance": attendance_data,
                    "exams": exam_data,
                    "behavior": behavior_data,
                    "fees": fee_data,
                    "leaves": leave_data,
                    "transport": transport_data,
                    "overall_stats": overall_stats
                }
            }

            return Response(self._to_json_safe(response_data), status=200)

        except Student.DoesNotExist:
            return Response({"error": f"Student with ID '{student_id}' not found"}, status=404)
        except Exception as e:
            logger.exception("Error in StudentOverviewView for student_id=%s", student_id)
            return Response({
                "error": "An error occurred while fetching student details",
                "message": str(e)
            }, status=500)

    # ================= HELPER METHODS =================

    def calculate_age(self, dob):
        """Calculate age from date of birth"""
        if not dob:
            return None
        today = date.today()
        age = today.year - dob.year
        if today.month < dob.month or (today.month == dob.month and today.day < dob.day):
            age -= 1
        return age

    def _to_json_safe(self, payload):
        """
        Convert nested Django/Python objects (date, datetime, Decimal) into
        JSON-native values before encryption middleware serializes response.data.
        """
        return json.loads(json.dumps(payload, cls=DjangoJSONEncoder))

    def get_transport_details(self, student):
        """Get transport allocation, bus/stop details, and today's bus attendance for a student."""
        today = date.today()

        default_payload = {
            "is_assigned": False,
            "message": "Student is not assigned to any bus",
            "bus": None,
            "stop": None,
            "today_bus_attendance": {
                "date": today.isoformat(),
                "morning": {
                    "is_marked": False,
                    "status": "Not Marked",
                    "marked_by": None,
                    "updated_at": None,
                },
                "evening": {
                    "is_marked": False,
                    "status": "Not Marked",
                    "marked_by": None,
                    "updated_at": None,
                },
                "overall_status": "Not Marked",
            },
        }

        try:
            allocation = (
                TransportAllocation.objects
                .filter(student=student)
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
                    "is_live": bool(route.is_active) if route else False,
                },
                "stop": {
                    "stop_id": stop.id if stop else None,
                    "stop_name": stop.stop_name if stop else None,
                    "order_number": stop.order_number if stop else None,
                    "arrival_time": stop.arrival_time.strftime("%H:%M:%S") if stop and stop.arrival_time else None,
                    "latitude": stop.latitude if stop else None,
                    "longitude": stop.longitude if stop else None,
                },
                "today_bus_attendance": {
                    "date": today.isoformat(),
                    "morning": {
                        "is_marked": bool(morning_rec),
                        "status": morning_status,
                        "marked_by": morning_rec.marked_by.name if morning_rec and morning_rec.marked_by else None,
                        "updated_at": morning_rec.updated_at if morning_rec else None,
                    },
                    "evening": {
                        "is_marked": bool(evening_rec),
                        "status": evening_status,
                        "marked_by": evening_rec.marked_by.name if evening_rec and evening_rec.marked_by else None,
                        "updated_at": evening_rec.updated_at if evening_rec else None,
                    },
                    "overall_status": overall_status,
                },
            }
        except Exception:
            logger.exception("Error in get_transport_details for student_id=%s", getattr(student, "student_id", None))
            return {
                **default_payload,
                "message": "Unable to load transport details",
            }

    def get_class_teacher(self, section, academic_year):
        """Get class teacher for the section"""
        try:
            ct = ClassTeacher.objects.get(
                section=section,
                academic_year=academic_year
            )
            return {
                "name": ct.teacher.name if ct.teacher else "Not Assigned",
                "teacher_id": ct.teacher.teacher_id if ct.teacher else None
            }
        except ClassTeacher.DoesNotExist:
            return {"name": "Not Assigned", "teacher_id": None}
        except ClassTeacher.MultipleObjectsReturned:
            # Data issue safety: pick first active mapping without breaking response.
            ct = ClassTeacher.objects.filter(
                section=section,
                academic_year=academic_year
            ).select_related('teacher').first()
            if not ct:
                return {"name": "Not Assigned", "teacher_id": None}
            return {
                "name": ct.teacher.name if ct.teacher else "Not Assigned",
                "teacher_id": ct.teacher.teacher_id if ct.teacher else None
            }
        except Exception:
            logger.exception(
                "Error fetching class teacher for section_id=%s, year_id=%s",
                getattr(section, "id", None),
                getattr(academic_year, "id", None),
            )
            return {"name": "Not Assigned", "teacher_id": None}

    def get_attendance_summary(self, enrollment, academic_year):
        """Get attendance summary for current academic year"""
        today = date.today()

        def empty_attendance_payload():
            return {
                "today": {
                    "date": today.isoformat(),
                    "status": "Not Marked",
                    "is_marked": False,
                    "remarks": None
                },
                "overall": {
                    "total_days_marked": 0,
                    "total_days_passed": 0,
                    "actual_working_days": 0,
                    "sundays": 0,
                    "holidays": 0,
                    "holiday_dates": [],
                    "present": 0,
                    "absent": 0,
                    "late": 0,
                    "attendance_percentage": 0.0
                },
                "current_month": {
                    "month": today.strftime("%B %Y"),
                    "present": 0,
                    "absent": 0,
                    "late": 0,
                    "sundays": 0,
                    "holidays": 0,
                    "working_days": 0,
                    "attendance_percentage": 0.0
                },
                "last_updated": None
            }

        try:
            period_start = academic_year.start_date
            period_end = min(academic_year.end_date, today)

            if period_start > period_end:
                return empty_attendance_payload()

            attendance_records = StudentAttendance.objects.filter(
                enrollment=enrollment,
                date__range=[period_start, period_end]
            ).order_by('-date')

            attendance_map = {rec.date: rec for rec in attendance_records}
            holiday_qs = Holiday.objects.filter(
                date__range=[period_start, period_end]
            ).filter(Q(applicable_for='everyone') | Q(applicable_for='students_only'))
            holiday_list = list(holiday_qs.values_list('date', flat=True))
            holiday_set = set(holiday_list)

            current_month = today.month
            current_year = today.year

            total_days_passed = (period_end - period_start).days + 1
            total_days_marked = attendance_records.count()
            present_count = 0
            absent_count = 0
            late_count = 0
            sundays = 0
            holidays = 0

            month_present = 0
            month_absent = 0
            month_late = 0
            month_sundays = 0
            month_holidays = 0
            month_days_passed = 0

            current_day = period_start
            while current_day <= period_end:
                rec = attendance_map.get(current_day)
                is_month = (current_day.month == current_month and current_day.year == current_year)
                if is_month:
                    month_days_passed += 1

                if rec:
                    status_key = (rec.status or "").strip().lower()
                    if status_key == 'present':
                        present_count += 1
                        if is_month:
                            month_present += 1
                    elif status_key == 'late':
                        late_count += 1
                        if is_month:
                            month_late += 1
                    else:
                        absent_count += 1
                        if is_month:
                            month_absent += 1
                elif current_day.weekday() == 6:
                    sundays += 1
                    if is_month:
                        month_sundays += 1
                elif current_day in holiday_set:
                    holidays += 1
                    if is_month:
                        month_holidays += 1
                else:
                    absent_count += 1
                    if is_month:
                        month_absent += 1

                current_day += timedelta(days=1)

            actual_working_days = max(0, total_days_passed - sundays - holidays)
            month_working_days = max(0, month_days_passed - month_sundays - month_holidays)

            overall_percentage = ((present_count + late_count) / actual_working_days * 100) if actual_working_days > 0 else 0.0
            month_percentage = ((month_present + month_late) / month_working_days * 100) if month_working_days > 0 else 0.0

            today_rec = attendance_map.get(today)

            return {
                "today": {
                    "date": today.isoformat(),
                    "status": today_rec.status if today_rec else "Not Marked",
                    "is_marked": bool(today_rec),
                    "remarks": today_rec.remarks if today_rec else None
                },
                "overall": {
                    "total_days_marked": total_days_marked,
                    "total_days_passed": total_days_passed,
                    "actual_working_days": actual_working_days,
                    "sundays": sundays,
                    "holidays": holidays,
                    "holiday_dates": [d.strftime("%Y-%m-%d") for d in holiday_list],
                    "present": present_count,
                    "absent": absent_count,
                    "late": late_count,
                    "attendance_percentage": round(overall_percentage, 1)
                },
                "current_month": {
                    "month": today.strftime("%B %Y"),
                    "present": month_present,
                    "absent": month_absent,
                    "late": month_late,
                    "sundays": month_sundays,
                    "holidays": month_holidays,
                    "working_days": month_working_days,
                    "attendance_percentage": round(month_percentage, 1)
                },
                "last_updated": attendance_records.first().date if attendance_records.exists() else None,
            }
        except Exception:
            logger.exception("Error in get_attendance_summary for enrollment_id=%s", getattr(enrollment, "id", None))
            return empty_attendance_payload()

    def get_exam_performance(self, enrollment, academic_year):
        """Get exam performance data"""
        try:
            today = date.today()
            current_standard = enrollment.section.standard if enrollment and enrollment.section else None

            schedules = ExamSchedule.objects.filter(
                academic_year=academic_year,
                classes=current_standard
            ).select_related(
                'exam_type', 'exam_type__term'
            ).prefetch_related(
                'details'
            ).distinct().order_by('start_date', 'exam_type__term__rank', 'exam_type__rank')

            exam_buckets = {
                "upcoming": [],
                "current": [],
                "finished": []
            }

            for schedule in schedules:
                exam_info = {
                    "exam_type": schedule.exam_type.name if schedule.exam_type else None,
                    "term": schedule.exam_type.term.name if schedule.exam_type and schedule.exam_type.term else None,
                    "start_date": schedule.start_date.isoformat() if schedule.start_date else None,
                    "end_date": schedule.end_date.isoformat() if schedule.end_date else None,
                    "subjects": [{
                        "subject_name": detail.subject_name,
                        "exam_date": detail.exam_date.isoformat() if detail.exam_date else None,
                        "session": detail.session,
                        "duration": detail.duration
                    } for detail in schedule.details.all().order_by('exam_date')]
                }

                if schedule.start_date and schedule.end_date and schedule.start_date <= today <= schedule.end_date:
                    exam_buckets["current"].append(exam_info)
                elif schedule.end_date and schedule.end_date < today:
                    exam_buckets["finished"].append(exam_info)
                else:
                    exam_buckets["upcoming"].append(exam_info)

            # StudentMark uses `schedule` FK; derive exam/term safely from schedule.exam_type.
            marks = StudentMark.objects.filter(
                enrollment=enrollment
            ).select_related('subject', 'schedule', 'schedule__exam_type', 'schedule__exam_type__term')

            subject_wise = defaultdict(list)

            for mark in marks:
                exam_type = mark.schedule.exam_type if mark.schedule else None
                exam_term = exam_type.term if exam_type else None
                subject_wise[mark.subject.name].append({
                    "exam": exam_type.name if exam_type else None,
                    "term": exam_term.name if exam_term else None,
                    "marks_obtained": float(mark.marks_obtained),
                    "total_marks": float(mark.total_marks),
                    "grade": mark.grade_point
                })
            
            subject_averages = []
            for subject_name, subject_marks in subject_wise.items():
                total_obtained = sum(m["marks_obtained"] for m in subject_marks)
                total_max = sum(m["total_marks"] for m in subject_marks)
                avg_percentage = (total_obtained / total_max * 100) if total_max > 0 else 0

                subject_averages.append({
                    "subject": subject_name,
                    "average_percentage": round(avg_percentage, 1),
                    "total_exams": len(subject_marks)
                })

            subject_averages.sort(key=lambda x: x["average_percentage"], reverse=True)

            return {
                "total_exams_taken": marks.values('schedule').distinct().count(),
                "upcoming_exams": exam_buckets["upcoming"],
                "current_exams": exam_buckets["current"],
                "finished_exams": exam_buckets["finished"],
                "exam_status_summary": {
                    "upcoming": len(exam_buckets["upcoming"]),
                    "current": len(exam_buckets["current"]),
                    "finished": len(exam_buckets["finished"])
                },
                "subject_ranking": subject_averages,
                "best_subject": subject_averages[0] if subject_averages else None,
                "needs_improvement": subject_averages[-1] if len(subject_averages) > 1 else None,
                "overall_average": round(sum(s["average_percentage"] for s in subject_averages) / len(subject_averages), 1) if subject_averages else 0
            }
        except Exception:
            logger.exception(
                "Error in get_exam_performance for enrollment_id=%s",
                getattr(enrollment, "id", None)
            )
            return {
                "total_exams_taken": 0,
                "upcoming_exams": [],
                "current_exams": [],
                "finished_exams": [],
                "exam_status_summary": {
                    "upcoming": 0,
                    "current": 0,
                    "finished": 0
                },
                "subject_ranking": [],
                "best_subject": None,
                "needs_improvement": None,
                "overall_average": 0
            }

    def get_behavior_reports(self, enrollment):
        """Get behavior reports"""
        reports = BehaviorReport.objects.filter(
            enrollment=enrollment
        ).select_related('subject', 'term')
        
        if not reports.exists():
            return {"message": "No behavior reports found", "reports": []}
        
        behavior_data = []
        term_wise = defaultdict(list)
        
        for report in reports:
            report_data = {
                "term": report.term.name,
                "subject": report.subject.name,
                "participation": report.participation,
                "responsibility": report.responsibility,
                "discipline": report.discipline,
                "attitude": report.attitude,
                "collaboration": report.collaboration,
                "average_score": report.average_score,
                "remarks": report.remarks,
                "posted_by": report.teacher.username if report.teacher else "Unknown",
                "date": report.created_at.date()
            }
            behavior_data.append(report_data)
            term_wise[report.term.name].append(report.average_score)
        
        # Calculate term averages
        term_averages = []
        for term_name, scores in term_wise.items():
            term_averages.append({
                "term": term_name,
                "average_score": round(sum(scores) / len(scores), 1),
                "total_subjects": len(scores)
            })
        
        # Get overall behavior score
        all_scores = [r.average_score for r in reports]
        overall_avg = round(sum(all_scores) / len(all_scores), 1) if all_scores else 0
        
        # Behavior rating
        if overall_avg >= 4.5:
            rating = "Excellent"
        elif overall_avg >= 4.0:
            rating = "Very Good"
        elif overall_avg >= 3.5:
            rating = "Good"
        elif overall_avg >= 3.0:
            rating = "Satisfactory"
        elif overall_avg >= 2.5:
            rating = "Needs Improvement"
        else:
            rating = "Concern"
        
        return {
            "total_reports": len(behavior_data),
            "overall_score": overall_avg,
            "behavior_rating": rating,
            "term_averages": term_averages,
            "reports": behavior_data,
            "last_updated": reports.latest('created_at').created_at.date() if reports.exists() else None
        }

    def get_fee_status(self, student, academic_year):
        """Get fee payment status"""
        try:
            fee_records = StudentFee.objects.filter(
                student=student,
                fee_definition__academic_year=academic_year.name if hasattr(academic_year, 'name') else str(academic_year)
            ).select_related('fee_definition').prefetch_related('payments')
            
            if not fee_records.exists():
                return {"message": "No fee records found", "fees": []}
            
            fee_data = []
            total_due = 0
            total_paid = 0
            total_concession = 0
            
            for fee in fee_records:
                fee_info = {
                    "fee_type": fee.fee_definition.fee_type,
                    "total_amount": float(fee.total_amount),
                    "paid_amount": float(fee.paid_amount),
                    "concession": float(fee.concession_amount),
                    "due_amount": float(fee.due_amount),
                    "status": fee.status,
                    "due_date": fee.fee_definition.due_date,
                    "installments": fee.payments.count()
                }
                fee_data.append(fee_info)
                
                total_due += fee.due_amount
                total_paid += fee.paid_amount
                total_concession += fee.concession_amount
            
            # Payment history
            all_payments = FeePayment.objects.filter(
                student_fee__in=fee_records
            ).select_related('student_fee__fee_definition').order_by('-payment_date')[:10]
            
            payment_history = []
            for payment in all_payments:
                payment_history.append({
                    "date": payment.payment_date,
                    "amount": float(payment.amount_paid),
                    "mode": payment.payment_mode,
                    "transaction_id": payment.transaction_id,
                    "fee_type": payment.student_fee.fee_definition.fee_type
                })
            
            return {
                "total_fees": len(fee_data),
                "total_amount": sum(float(f["total_amount"]) for f in fee_data),
                "total_paid": float(total_paid),
                "total_concession": float(total_concession),
                "total_due": float(total_due),
                "payment_status": "Cleared" if total_due == 0 else "Pending" if total_due > 0 else "Overpaid",
                "fee_details": fee_data,
                "recent_payments": payment_history,
                "payment_summary": {
                    "cleared": len([f for f in fee_data if f["status"] == "PAID"]),
                    "partial": len([f for f in fee_data if f["status"] == "PARTIAL"]),
                    "unpaid": len([f for f in fee_data if f["status"] == "UNPAID"])
                }
            }
        except Exception:
            logger.exception("Error in get_fee_status for student_id=%s", getattr(student, "student_id", None))
            return {"message": "No fee records found", "fees": []}

    def get_leave_history(self, student, academic_year):
        """Get leave history"""
        try:
            leaves = LeaveRequest.objects.filter(
                student=student,
                academic_year=academic_year
            ).order_by('-created_at')
            
            if not leaves.exists():
                return {"message": "No leave records found", "leaves": []}
            
            leave_data = []
            approved_count = 0
            pending_count = 0
            rejected_count = 0
            
            for leave in leaves:
                leave_info = {
                    "start_date": leave.start_date,
                    "end_date": leave.end_date,
                    "duration_days": (leave.end_date - leave.start_date).days + 1,
                    "reason": leave.reason,
                    "status": leave.status,
                    "approved_by": leave.approved_by_name,
                    "admin_comment": leave.admin_comment,
                    "applied_date": leave.created_at.date(),
                    "has_proof": bool(leave.proof_file)
                }
                leave_data.append(leave_info)
                
                if leave.status == 'Approved':
                    approved_count += 1
                elif leave.status == 'Pending':
                    pending_count += 1
                elif leave.status == 'Rejected':
                    rejected_count += 1
            
            # Current year statistics
            current_year = date.today().year
            current_year_leaves = leaves.filter(start_date__year=current_year)
            days_taken = sum((l.end_date - l.start_date).days + 1 for l in current_year_leaves if l.status == 'Approved')
            
            return {
                "total_leaves": len(leave_data),
                "approved": approved_count,
                "pending": pending_count,
                "rejected": rejected_count,
                "days_taken_current_year": days_taken,
                "leave_details": leave_data,
                "recent_leaves": leave_data[:5],  # Last 5 leaves
                "current_status": "Active" if not any(l["status"] == "Pending" for l in leave_data[:3]) else "Has Pending Requests"
            }
        except Exception:
            logger.exception("Error in get_leave_history for student_id=%s", getattr(student, "student_id", None))
            return {"message": "No leave records found", "leaves": []}

    def calculate_overall_stats(self, attendance, exams, behavior, fees):
        """Calculate overall statistics"""
        # Attendance score (out of 100)
        attendance_score = attendance["overall"]["attendance_percentage"]
        
        # Exam score (out of 100)
        exam_score = exams.get("overall_average", 0)
        
        # Behavior score (convert 5-point scale to 100)
        behavior_score = (behavior.get("overall_score", 0) / 5) * 100
        
        # Fee compliance score (100 if no dues, 0 if full dues)
        total_due = fees.get("total_due", 0) or 0
        total_amount = fees.get("total_amount", 0) or 0
        if total_due == 0:
            fee_score = 100
        elif total_amount <= 0:
            fee_score = 0
        else:
            fee_score = max(0, 100 - (total_due / total_amount * 100))
        
        # Overall grade calculation
        overall_score = (attendance_score * 0.25 + 
                        exam_score * 0.35 + 
                        behavior_score * 0.25 + 
                        fee_score * 0.15)
        
        # Determine overall status
        if overall_score >= 90:
            overall_status = "Excellent"
            status_color = "emerald"
        elif overall_score >= 80:
            overall_status = "Very Good"
            status_color = "green"
        elif overall_score >= 70:
            overall_status = "Good"
            status_color = "blue"
        elif overall_score >= 60:
            overall_status = "Satisfactory"
            status_color = "amber"
        elif overall_score >= 50:
            overall_status = "Needs Improvement"
            status_color = "orange"
        else:
            overall_status = "Concern"
            status_color = "red"
        
        # Areas needing attention
        areas_attention = []
        if attendance_score < 75:
            areas_attention.append("Attendance")
        if exam_score < 60:
            areas_attention.append("Academic Performance")
        if behavior_score < 70:
            areas_attention.append("Behavior")
        if fee_score < 50:
            areas_attention.append("Fee Payment")
        
        return {
            "overall_score": round(overall_score, 1),
            "overall_status": overall_status,
            "status_color": status_color,
            "breakdown": {
                "attendance": round(attendance_score, 1),
                "academics": round(exam_score, 1),
                "behavior": round(behavior_score, 1),
                "fees": round(fee_score, 1)
            },
            "areas_needing_attention": areas_attention,
            "strengths": ["Good Behavior"] if behavior_score >= 80 else [],
            "last_updated": date.today().isoformat()
        }
