from calendar import monthrange
from datetime import datetime
import json

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from django.db import transaction, IntegrityError
from django.db.models import Q
from django.utils import timezone
from django.shortcuts import get_object_or_404
from collections import defaultdict
from django.core.serializers.json import DjangoJSONEncoder

# Permissions
from staff.permissions import IsOwnerAdminOrAdminStaff
from teachers.permissions import IsTeacher

# Models
from .models import StudentMark, ExamSchedule, ExamDetail, ExamType, ExamTerm, MarkChangeRequest
from students.models import Student, Enrollment
from school.models import AcademicYear
from teachers.models import Teacher, TeacherAllocation
from subjects.models import Subject
from academics.models import Standard, Section, ClassTeacher 
from timetable.models import TimetableSlot  # <--- CRITICAL NEW IMPORT
from django.contrib.auth import get_user_model
from notifications.utils import send_notification_to_users
from notifications.whatsapp import send_whatsapp_template

User = get_user_model()

# Serializers
from .serializers import ExamScheduleSerializer, ExamDetailUpdateSerializer, ExamTypeSerializer, ExamTermSerializer

def get_academic_year(request):
    """
    Determines the Academic Year context.
    - Admins: Can request specific year via ?year_id=X or ?year=Name. Defaults to Active.
    - Teachers/Students: LOCKED to Active Year.
    """
    # 1. Default to Active Year
    try:
        active_year = AcademicYear.objects.get(is_current=True)
    except AcademicYear.DoesNotExist:
        # Fallback for Admins setting up the system
        if IsOwnerAdminOrAdminStaff().has_permission(request, None):
            return None 
        raise # 500 Error for others

    # 2. Check if user is Admin
    if IsOwnerAdminOrAdminStaff().has_permission(request, None):
        requested_year = request.query_params.get('year_id') or request.query_params.get('year')
        
        if requested_year:
            try:
                if requested_year.isdigit():
                    return AcademicYear.objects.get(id=requested_year)
                else:
                    return AcademicYear.objects.get(name=requested_year)
            except AcademicYear.DoesNotExist:
                pass # Fallback to active year if not found
                
    return active_year

# Helper to calculate grade
def get_grade(percentage):
    if percentage is None: return "N/A"
    if percentage >= 91: return 'S'
    if percentage >= 81: return 'A'
    if percentage >= 71: return 'B'
    if percentage >= 61: return 'C'
    if percentage >= 51: return 'D'
    if percentage >= 40: return 'E'
    return 'F'

def _format_mark_number(value):
    try:
        as_float = float(value)
        return str(int(as_float)) if as_float.is_integer() else f"{as_float:g}"
    except (TypeError, ValueError):
        return str(value)

def send_student_mark_whatsapp(enrollment, subject, exam_obj, term_name, marks, total_marks):
    student = enrollment.student
    father_phone = getattr(student, 'father_phone', '') or ''
    if not father_phone:
        return False, {'error': 'Father phone number is missing.'}

    section = enrollment.section
    class_label = enrollment.standard.name
    if section:
        class_label = f'{class_label}-{section.name}'

    message = (
        f'Marks update: {student.student_name} ({student.student_id}) scored '
        f'{_format_mark_number(marks)}/{_format_mark_number(total_marks)} in {subject.name} '
        f'for {exam_obj.name} ({term_name}). Class: {class_label}.'
    )
    return send_whatsapp_template(father_phone, message, alert_type='exams')

# ==========================================
# 1. ADMIN: EXAM TYPE MANAGEMENT
# ==========================================
class AdminExamTermView(APIView):
    permission_classes = [IsAuthenticated, IsOwnerAdminOrAdminStaff]

    def get(self, request):
        active_year = get_academic_year(request)
        terms = ExamTerm.objects.filter(academic_year=active_year).order_by('rank')
        serializer = ExamTermSerializer(terms, many=True)
        return Response(serializer.data)

    def post(self, request):
        is_many = isinstance(request.data, list)
        serializer = ExamTermSerializer(data=request.data, many=is_many)
        if serializer.is_valid():
            serializer.save()
            return Response({"message": "Structure Created Successfully", "data": serializer.data}, 201)
        return Response(serializer.errors, 400)

    def put(self, request):
        term_id = request.data.get('id')
        new_name = request.data.get('name')
        new_rank = request.data.get('rank')
        exams_data = request.data.get('exams', [])

        if not term_id: 
            return Response({"error": "Term ID is required"}, 400)

        try:
            term = ExamTerm.objects.get(id=term_id)
        except ExamTerm.DoesNotExist:
            return Response({"error": "Term not found"}, 404)

        # 1. Term Name Edit Protection
        if new_name and new_name != term.name:
            if StudentMark.objects.filter(schedule__exam_type__term=term).exists():
                return Response({"error": "Cannot edit Term name because marks have already been uploaded for its exams."}, 400)
            term.name = new_name
            
        if new_rank: 
            term.rank = new_rank
            
        term.save()

        updated_exams = []
        seen_names = set()
        for ex_data in exams_data:
            name = (ex_data.get('name') or '').strip()
            if not name:
                return Response({"error": "Exam name is required"}, 400)
            key = name.lower()
            if key in seen_names:
                return Response({"error": f"Duplicate exam '{name}' in payload"}, 400)
            seen_names.add(key)

        try:
            with transaction.atomic():
                for ex_data in exams_data:
                    ex_id = ex_data.get('id')
                    exam_name = (ex_data.get('name') or '').strip()

                    if ex_id:
                        try:
                            exam_obj = ExamType.objects.get(id=ex_id, term=term)
                        except ExamType.DoesNotExist:
                            return Response({"error": f"Exam id {ex_id} not found in this term"}, 404)

                        # 2. Exam Name Edit Protection
                        new_exam_name = exam_name or exam_obj.name
                        if new_exam_name != exam_obj.name:
                            if StudentMark.objects.filter(schedule__exam_type=exam_obj).exists():
                                return Response({"error": f"Cannot edit Exam name '{exam_obj.name}' because marks are already uploaded."}, 400)
                            if ExamType.objects.filter(term=term, name__iexact=new_exam_name).exclude(id=exam_obj.id).exists():
                                return Response({"error": f"Exam '{new_exam_name}' already exists in this term."}, 400)

                        exam_obj.name = new_exam_name
                        exam_obj.max_marks = ex_data.get('max_marks', exam_obj.max_marks)
                        exam_obj.rank = ex_data.get('rank', exam_obj.rank)
                        exam_obj.save()
                        updated_exams.append(exam_obj.name)
                    else:
                        existing_exam = ExamType.objects.filter(term=term, name__iexact=exam_name).first()
                        if existing_exam:
                            # If frontend sends an existing exam without id, treat it as update to avoid duplicate crash.
                            existing_exam.max_marks = ex_data.get('max_marks', existing_exam.max_marks)
                            existing_exam.rank = ex_data.get('rank', existing_exam.rank)
                            existing_exam.save(update_fields=['max_marks', 'rank'])
                            updated_exams.append(f"Updated Existing: {existing_exam.name}")
                        else:
                            ExamType.objects.create(
                                term=term,
                                name=exam_name,
                                max_marks=ex_data.get('max_marks', 100),
                                rank=ex_data.get('rank', 0)
                            )
                            updated_exams.append(f"New: {exam_name}")
        except IntegrityError:
            return Response({"error": "Duplicate exam name in this term. Please use a unique exam name."}, 400)

        return Response({"message": "Term updated", "term": term.name, "updates_processed": updated_exams})
        
    def delete(self, request):
        term_id = request.query_params.get('id')
        exam_id = request.query_params.get('exam_id')

        # Scenario 1: Delete Specific Exam Only
        if exam_id:
            try:
                # Verify exam exists
                exam = ExamType.objects.get(id=exam_id)
                
                # Optional: Verify it belongs to the term if term_id is passed (Strictness)
                if term_id and str(exam.term.id) != str(term_id):
                    return Response({"error": "Exam does not belong to the provided Term ID"}, 400)

                # NEW: Exam Deletion Protection
                if StudentMark.objects.filter(schedule__exam_type=exam).exists():
                    return Response({"error": f"Cannot delete '{exam.name}'. Marks are already uploaded."}, 400)

                exam_name = exam.name
                exam.delete()
                return Response({"message": f"Exam '{exam_name}' deleted successfully."}, 200)
            except ExamType.DoesNotExist:
                return Response({"error": "Exam not found"}, 404)

        # Scenario 2: Delete Entire Term
        elif term_id:
            try:
                term_to_delete = ExamTerm.objects.get(id=term_id)
                
                # NEW: Term Deletion Protection
                if StudentMark.objects.filter(schedule__exam_type__term=term_to_delete).exists():
                    return Response({"error": "Cannot delete Term. Marks exist for exams under this term."}, 400)
                
                term_to_delete.delete()
                return Response({"message": "Term and all its exams deleted"}, 200)
            except ExamTerm.DoesNotExist:
                return Response({"error": "Term not found"}, 404)

        else:
            return Response({"error": "Please provide 'id' (Term) or 'exam_id' (Exam) to delete."}, 400)


class ExamDropdownView(APIView):
    permission_classes = [IsAuthenticated]
    def get(self, request):
        term_id = request.query_params.get('term_id')
        active_year = get_academic_year(request)
        exams = ExamType.objects.filter(term__academic_year=active_year).order_by('term__rank', 'rank')
        if term_id: exams = exams.filter(term_id=term_id)
        serializer = ExamTypeSerializer(exams, many=True)
        return Response(serializer.data)

# ==========================================
# 2. TEACHER: UPLOAD MARKS (TIMETABLE-SECURED)
# ==========================================
class UploadMarksView(APIView):
    permission_classes = [IsAuthenticated, IsTeacher]

    def post(self, request):
        # 1. ACADEMIC YEAR SAFETY
        try:
            active_year = AcademicYear.objects.get(is_current=True)
        except AcademicYear.DoesNotExist:
            return Response({"error": "Configuration Error: No Active Academic Year set."}, 500)
        except AcademicYear.MultipleObjectsReturned:
            return Response({"error": "Configuration Error: Multiple Active Years found."}, 500)

        # 2. QUERY PARAM VALIDATION
        class_param = request.query_params.get('class')
        section_param = request.query_params.get('section')
        
        if not all([class_param, section_param]):
             return Response({"error": "'class' and 'section' query parameters are required"}, 400)

        # 3. DATA VALIDATION
        term_name = request.data.get('term')
        exam_name = request.data.get('exam_type')
        subject_name = request.data.get('subject')
        marks_list = request.data.get('students_marks', [])

        if not all([term_name, exam_name, subject_name, marks_list]):
            return Response({"error": "Missing body params: term, exam_type, subject, students_marks"}, 400)

        teacher = request.user.teacher_profile
        results = []
        whatsapp_sent = 0
        whatsapp_failed = 0
        whatsapp_skipped = 0
        push_sent = 0

        # 4. OBJECT RETRIEVAL
        try:
            exam_obj = ExamType.objects.get(name__iexact=exam_name, term__name__iexact=term_name, term__academic_year=active_year)
        except ExamType.DoesNotExist:
            return Response({"error": f"Exam '{exam_name}' not found in '{term_name}'"}, 404)

        try:
            subject = Subject.objects.get(name__iexact=subject_name, standard__name=class_param)
        except Subject.DoesNotExist:
            return Response({"error": f"Subject '{subject_name}' not found for Class {class_param}"}, 404)

# 5. SCHEDULE VALIDATION
        try:
            schedule_obj = ExamSchedule.objects.get(
                exam_type=exam_obj,
                classes=subject.standard,
                academic_year=active_year
            )
        except ExamSchedule.DoesNotExist:
            return Response({"error": f"Exam '{exam_name}' not scheduled for Class {class_param} in {active_year.name}"}, 400)

        exam_detail = schedule_obj.details.filter(
            subject_name__iexact=subject_name
        ).order_by('exam_date').first()

        if not exam_detail or not exam_detail.exam_date:
            return Response({
                "error": f"Exam date is not configured for {subject_name} - {exam_name}."
            }, 400)

        today = timezone.localdate()
        if exam_detail.exam_date > today:
            return Response({
                "error": (
                    f"Marks cannot be entered before the exam date. "
                    f"{subject_name} exam is scheduled on {exam_detail.exam_date.isoformat()}."
                ),
                "exam_date": exam_detail.exam_date.isoformat(),
            }, 400)
            
        # 6. PERMISSION CHECK (SUBJECT TEACHER ONLY)
        try:
            target_section = Section.objects.get(standard__name=class_param, name=section_param)
            
            # This logic strictly checks the Timetable for the Subject-Teacher-Section mapping
            has_permission = TimetableSlot.objects.filter(
                academic_year=active_year,
                teacher=teacher,
                section=target_section,
                subject=subject
            ).exists()
            
            if not has_permission:
                return Response({
                    "error": f"Permission Denied: You are not assigned to teach {subject_name} to {class_param}-{section_param}."
                }, 403)
                    
        except Section.DoesNotExist:
            return Response({"error": "Invalid Class/Section"}, 404)

        # 7. PROCESSING MARKS
        for entry in marks_list:
            s_id = entry.get('student_id')
            raw_marks = entry.get('marks')
            
            if raw_marks is None:
                results.append({"student_id": s_id, "status": "Failed", "error": "Marks missing"})
                continue
            
            try:
                score = float(raw_marks)
            except ValueError:
                results.append({"student_id": s_id, "status": "Failed", "error": "Invalid format"})
                continue

            if score < 0 or score > exam_obj.max_marks:
                results.append({"student_id": s_id, "status": "Failed", "error": f"Marks must be between 0 and {exam_obj.max_marks}"})
                continue

            try:
                # Get Enrollment for active year
                enrollment = Enrollment.objects.get(
                    student__student_id=s_id,
                    academic_year=active_year,
                    is_active=True
                )

                if enrollment.section != target_section:
                    results.append({"student_id": s_id, "status": "Failed", "error": "Student section mismatch"})
                    continue

                # Save or Update Mark
                StudentMark.objects.update_or_create(
                    enrollment=enrollment,
                    subject=subject,
                    schedule=schedule_obj,  # <--- CHANGED FROM exam_type TO schedule
                    defaults={'marks_obtained': score, 'total_marks': exam_obj.max_marks}
                )

                whatsapp_ok, whatsapp_response = send_student_mark_whatsapp(
                    enrollment,
                    subject,
                    exam_obj,
                    term_name,
                    score,
                    exam_obj.max_marks,
                )
                whatsapp_error = str(whatsapp_response.get('error', '')) if isinstance(whatsapp_response, dict) else ''
                whatsapp_disabled_or_unconfigured = (
                    whatsapp_error.startswith('WhatsApp alert disabled:')
                    or whatsapp_error == 'WhatsApp alerts are not configured.'
                    or whatsapp_error == 'Father phone number is missing.'
                )

                if whatsapp_ok:
                    whatsapp_sent += 1
                elif whatsapp_disabled_or_unconfigured:
                    whatsapp_skipped += 1
                else:
                    whatsapp_failed += 1

                if enrollment.student.user:
                    try:
                        send_notification_to_users(
                            users=[enrollment.student.user],
                            sender=request.user,
                            title=f"Marks Uploaded: {subject.name}",
                            message=(
                                f"Your {exam_obj.name} marks for {subject.name} "
                                f"are {_format_mark_number(score)}/{_format_mark_number(exam_obj.max_marks)}."
                            ),
                            notif_type="Exam Result",
                            data={
                                "screen": "student_performance",
                                "permission": "performance",
                                "subject": subject.name,
                                "exam": exam_obj.name,
                                "term": term_name,
                            },
                        )
                        push_sent += 1
                    except Exception as e:
                        print(f"Notification Error: {e}")

                result_row = {
                    "student_id": s_id,
                    "status": "Success",
                    "whatsapp_sent": whatsapp_ok,
                }
                if whatsapp_error:
                    result_row["whatsapp_error"] = whatsapp_error
                results.append(result_row)

            except Enrollment.DoesNotExist:
                results.append({"student_id": s_id, "status": "Failed", "error": "Student not enrolled in active year"})
            except Exception as e:
                results.append({"student_id": s_id, "status": "Failed", "error": str(e)})

        return Response({
            "message": "Processing Complete",
            "report": results,
            "whatsapp_sent": whatsapp_sent,
            "whatsapp_failed": whatsapp_failed,
            "whatsapp_skipped": whatsapp_skipped,
            "push_sent": push_sent,
        }, 200)

# ==========================================
# 3. CLASS RESULTS (ADMIN TIME-TRAVEL ENABLED)
# ==========================================
class ClassExamResultView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        # 1. Use Helper to support History View
        active_year = get_academic_year(request)
        if not active_year: return Response({"error": "No Active Year Configured"}, 500)

        class_name = request.query_params.get('class')
        section_name = request.query_params.get('section')
        exam_name = request.query_params.get('exam_type')
        term_name = request.query_params.get('term')

        if not all([class_name, section_name, exam_name, term_name]):
            return Response({"error": "Missing params"}, 400)

        try:
            exam_obj = ExamType.objects.get(name__iexact=exam_name, term__name__iexact=term_name, term__academic_year=active_year)
        except ExamType.DoesNotExist:
            return Response({"error": "Exam not found"}, 404)

        # --- CORRECTED AUTH CHECK ---
        is_authorized = False
        if IsOwnerAdminOrAdminStaff().has_permission(request, self):
            is_authorized = True
        elif hasattr(request.user, 'teacher_profile'):
            teacher = request.user.teacher_profile
            # Logic: Check if Teacher is Class Teacher for this section in THIS year
            try:
                ct = ClassTeacher.objects.get(teacher=teacher, academic_year=active_year)
                if ct.section.standard.name == class_name and ct.section.name == section_name:
                    is_authorized = True
            except ClassTeacher.DoesNotExist:
                pass # Not a class teacher, permission remains False
        
        if not is_authorized:
            return Response({"error": "Unauthorized"}, 403)

        # Fetch Enrollments for the SELECTED Year (Active or Past)
        enrollments = Enrollment.objects.filter(
            section__standard__name=class_name,
            section__name=section_name,
            academic_year=active_year 
        ).select_related('student')

        if not enrollments.exists():
             return Response({"error": f"No records found for Class {class_name}-{section_name} in {active_year.name}"}, 404)

        report_card = []
        grade_stats = defaultdict(int)

        for enroll in enrollments:
            marks = StudentMark.objects.filter(enrollment=enroll, schedule__exam_type=exam_obj)
            
            if marks.exists():
                total_obt = sum([m.marks_obtained for m in marks])
                total_max = sum([m.total_marks for m in marks])
                percentage = (total_obt / total_max * 100) if total_max > 0 else 0
                grade = get_grade(percentage)
                avg_display = round(total_obt, 2)
            else:
                avg_display = None
                grade = "N/A"

            grade_stats[grade] += 1
            
            report_card.append({
                "student_id": enroll.student.student_id,
                "name": enroll.student.student_name,
                "summative_total": avg_display,
                "overall_grade": grade
            })

        total_students = len(enrollments)
        pass_count = sum([grade_stats[g] for g in ["S", "A", "B", "C", "D", "E"]])

        return Response({
            "status": 200,
            "class": f"{class_name} - {section_name}",
            "academic_year": active_year.name,
            "exam": f"{exam_name} ({term_name})",
            "analytics": {
                "total_students": total_students,
                "total_pass": pass_count,
                "grade_breakdown": grade_stats
            },
            "data": report_card
        })

# ==========================================
# 4. SUBJECT ANALYSIS (TIMETABLE-SECURED)
# ==========================================
class SubjectExamAnalysisView(APIView):
    permission_classes = [IsAuthenticated, IsTeacher]

    def get(self, request):
        try:
            active_year = AcademicYear.objects.get(is_current=True)
        except AcademicYear.DoesNotExist:
            return Response({"error": "No Active Academic Year"}, 500)

        cls = request.query_params.get('class')
        sec = request.query_params.get('section')
        sub = request.query_params.get('subject')
        exam_name = request.query_params.get('exam_type')
        term_name = request.query_params.get('term')

        if not all([cls, sec, sub, exam_name]):
            return Response({"error": "Missing params"}, 400)

        teacher = request.user.teacher_profile
        
        # --- CORRECTED ALLOCATION CHECK (TIMETABLE) ---
        try:
            target_section = Section.objects.get(standard__name=cls, name=sec)
            target_subject = Subject.objects.get(name__iexact=sub, standard__name=cls)
            
            has_permission = TimetableSlot.objects.filter(
                academic_year=active_year,
                teacher=teacher,
                section=target_section,
                subject=target_subject
            ).exists()
            
            if not has_permission:
                # Allow Class Teachers to view analysis too
                is_ct = ClassTeacher.objects.filter(teacher=teacher, section=target_section, academic_year=active_year).exists()
                if not is_ct:
                     return Response({"error": "Permission Denied: Not your class/subject."}, 403)
                     
        except (Section.DoesNotExist, Subject.DoesNotExist):
             return Response({"error": "Invalid Class/Subject"}, 404)

        try:
            if term_name:
                exam_obj = ExamType.objects.get(name__iexact=exam_name, term__name__iexact=term_name, term__academic_year=active_year)
            else:
                exam_obj = ExamType.objects.filter(name__iexact=exam_name, term__academic_year=active_year).first()
                if not exam_obj: raise ExamType.DoesNotExist
        except ExamType.DoesNotExist:
            return Response({"error": "Exam not found"}, 404)

        # YEAR-SAFE: Query Marks via Enrollment
        marks = StudentMark.objects.filter(
            enrollment__section=target_section,
            enrollment__academic_year=active_year,
            subject=target_subject,
            schedule__exam_type=exam_obj
        ).select_related('enrollment__student')

        student_list = []
        grade_counts = defaultdict(int)
        pass_count = 0
        fail_count = 0

        for m in marks:
            grade = m.grade_point
            grade_counts[grade] += 1
            if grade == 'F': fail_count += 1
            else: pass_count += 1

            student_list.append({
                "student_id": m.enrollment.student.student_id,
                "name": m.enrollment.student.student_name,
                "mark": m.marks_obtained,
                "total": m.total_marks,
                "grade": grade
            })

        total_students = len(student_list)
        pass_percentage = round((pass_count / total_students) * 100, 1) if total_students > 0 else 0

        return Response({
            "exam": f"{exam_obj.name} ({exam_obj.term.name})",
            "class": f"{cls}-{sec}",
            "subject": sub,
            "stats": {
                "total_students": total_students,
                "pass_percentage": f"{pass_percentage}%",
                "grade_distribution": grade_counts,
                "pass_count": pass_count,
                "fail_count": fail_count
            },
            "students": student_list
        })

# ==========================================
# 5. STUDENT DETAIL (YEAR-SAFE)
# ==========================================
class StudentExamDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        try:
            active_year = AcademicYear.objects.get(is_current=True)
        except AcademicYear.DoesNotExist:
            return Response({"error": "No Active Academic Year"}, 500)

        target_id = request.query_params.get('student_id')
        exam_name = request.query_params.get('exam_type')
        term_name = request.query_params.get('term')

        if not target_id:
             return Response({"error": "Missing params: student_id is required"}, 400)

        if exam_name and exam_name.lower() == 'all':
            exam_name = None
        if term_name and term_name.lower() == 'all':
            term_name = None

        try:
            # YEAR-SAFE: Fetch Enrollment
            enrollment = Enrollment.objects.get(
                student__student_id=target_id,
                academic_year=active_year
            )
        except Enrollment.DoesNotExist:
            return Response({"error": "Student not enrolled in Active Year"}, 404)

        standard_obj = enrollment.standard or (enrollment.section.standard if enrollment.section else None)
        class_name = standard_obj.name if standard_obj else ""
        section_name = enrollment.section.name if enrollment.section else ""
        profile_image_url = (
            enrollment.student.profile_image.url
            if enrollment.student.profile_image
            else None
        )

        base_exams_qs = ExamType.objects.filter(term__academic_year=active_year)
        if term_name:
            base_exams_qs = base_exams_qs.filter(term__name__iexact=term_name)
        if exam_name:
            base_exams_qs = base_exams_qs.filter(name__iexact=exam_name)

        marked_exams_qs = base_exams_qs.filter(
            examschedule__marks__enrollment=enrollment
        ).distinct()

        resolved_exams_qs = marked_exams_qs if marked_exams_qs.exists() else base_exams_qs
        resolved_exams = list(resolved_exams_qs.order_by('-term__rank', '-rank'))

        if not resolved_exams:
            return Response({"error": "Exam not found for the selected filters"}, 404)

        all_subjects = Subject.objects.filter(standard=standard_obj) if standard_obj else Subject.objects.none()
        exams_payload = []

        for exam_obj in resolved_exams:
            result_data = []
            for sub in all_subjects:
                try:
                    mark_entry = StudentMark.objects.get(
                        enrollment=enrollment,
                        subject=sub,
                        schedule__exam_type=exam_obj
                    )
                    data = {
                        "subject": sub.name,
                        "marks": mark_entry.marks_obtained,
                        "max_marks": mark_entry.total_marks,
                        "grade": mark_entry.grade_point
                    }
                except StudentMark.DoesNotExist:
                    data = {"subject": sub.name, "marks": None, "grade": "-"}
                result_data.append(data)

            exams_payload.append({
                "exam": f"{exam_obj.name} ({exam_obj.term.name})",
                "subjects": result_data
            })

        if len(exams_payload) == 1:
            return Response({
                "status": 200,
                "student": enrollment.student.student_name,
                "profile_image": profile_image_url,
                "class": class_name,
                "section": section_name,
                "exam": exams_payload[0]["exam"],
                "subjects": exams_payload[0]["subjects"]
            })

        return Response({
            "status": 200,
            "student": enrollment.student.student_name,
            "profile_image": profile_image_url,
            "class": class_name,
            "section": section_name,
            "exam": "Multiple Exams",
            "subjects": [],
            "exams": exams_payload
        })

# ==========================================
# 6. EDIT MARKS (TIMETABLE-SECURED)
# ==========================================
class EditStudentMarkView(APIView):
    permission_classes = [IsAuthenticated]

    def put(self, request):
        user = request.user
        try:
            active_year = AcademicYear.objects.get(is_current=True)
        except AcademicYear.DoesNotExist:
            return Response({"error": "No Active Academic Year"}, 500)

        s_id = request.data.get('student_id')
        term_name = request.data.get('term')
        exam_name = request.data.get('exam_type')
        sub_name = request.data.get('subject')
        new_marks = request.data.get('marks')
        reason = request.data.get('reason', 'Correction')

        if not all([s_id, term_name, exam_name, sub_name, new_marks is not None]):
            return Response({"error": "Missing params"}, 400)

        try:
            exam_obj = ExamType.objects.get(name__iexact=exam_name, term__name__iexact=term_name, term__academic_year=active_year)
            
            # YEAR-SAFE: Enrollment Lookup
            enrollment = Enrollment.objects.get(student__student_id=s_id, academic_year=active_year)
            
            subject = Subject.objects.get(name__iexact=sub_name, standard=enrollment.section.standard)
            mark_entry = StudentMark.objects.get(enrollment=enrollment, subject=subject, schedule__exam_type=exam_obj)

        except (ExamType.DoesNotExist, Enrollment.DoesNotExist, Subject.DoesNotExist, StudentMark.DoesNotExist):
            return Response({"error": "Mark Entry not found for active year"}, 404)

        try:
            new_marks_float = float(new_marks)
        except ValueError:
             return Response({"error": "Invalid marks"}, 400)

        if new_marks_float > float(exam_obj.max_marks) or new_marks_float < 0:
            return Response({"error": "Marks out of range"}, 400)

        # STRICT PERMISSION LOGIC (TIMETABLE)
        if not hasattr(user, 'teacher_profile'):
            return Response({"error": "Admins cannot edit directly."}, 403)

        teacher = user.teacher_profile
        has_permission = TimetableSlot.objects.filter(
            academic_year=active_year,
            teacher=teacher,
            section=enrollment.section,
            subject=subject
        ).exists()
        
        if not has_permission:
            return Response({"error": "Unauthorized: You do not teach this subject to this class."}, 403)

        # 1. CREATE REQUEST (Assign to variable 'req')
        req = MarkChangeRequest.objects.create(
            enrollment=enrollment, 
            schedule=mark_entry.schedule,
            subject=subject,
            requested_by=teacher,
            old_marks=mark_entry.marks_obtained,
            new_marks=new_marks,
            reason=reason,
            status='PENDING'
        )

        # 2. NOTIFY ADMINS (New Code)
        try:
            admins = User.objects.filter(user_type__in=['admin', 'super_admin'])
            send_notification_to_users(
                users=admins,
                sender=user,
                title="Mark Change Request",
                message=f"Teacher {teacher.name} requests mark change for {enrollment.student.student_name} ({subject.name}).",
                notif_type="Mark Request"
            )
        except Exception as e:
            print(f"Notification Error: {e}")

            ##notification block ends 

        return Response({
            "message": "Edit request sent to Admin for approval.",
            "student": enrollment.student.student_name,
            "term": term_name,
            "old_marks": mark_entry.marks_obtained,
            "requested_marks": new_marks
        }, 202)

# ==========================================
# 7. CLASS TEACHER STUDENT MARKS (YEAR-SAFE)
# ==========================================
class ClassTeacherStudentMarksView(APIView):
    permission_classes = [IsAuthenticated, IsTeacher]

    def get(self, request):
        try:
            active_year = AcademicYear.objects.get(is_current=True)
        except AcademicYear.DoesNotExist:
            return Response({"error": "No Active Academic Year"}, 500)

        s_id = request.query_params.get('student_id')
        exam_name = request.query_params.get('exam_type')
        term_name = request.query_params.get('term')

        if not all([s_id, exam_name]): return Response({"error": "Missing params"}, 400)

        try:
            if term_name:
                exam_obj = ExamType.objects.get(name__iexact=exam_name, term__name__iexact=term_name, term__academic_year=active_year)
            else:
                exam_obj = ExamType.objects.filter(name__iexact=exam_name, term__academic_year=active_year).first()
                if not exam_obj: raise ExamType.DoesNotExist
        except ExamType.DoesNotExist:
            return Response({"error": "Exam not found"}, 404)

        try:
            # YEAR-SAFE: Enrollment
            enrollment = Enrollment.objects.get(student__student_id=s_id, academic_year=active_year)
        except Enrollment.DoesNotExist:
            return Response({"error": "Student not enrolled"}, 404)

        teacher = request.user.teacher_profile
        
        # --- CORRECTED CLASS TEACHER CHECK ---
        try:
            ct = ClassTeacher.objects.get(teacher=teacher, academic_year=active_year)
            if ct.section != enrollment.section:
                return Response({"error": "Not Class Teacher for this student"}, 403)
        except ClassTeacher.DoesNotExist:
            return Response({"error": "You are not a Class Teacher"}, 403)

        marks_qs = StudentMark.objects.filter(enrollment=enrollment, schedule__exam_type=exam_obj).select_related('subject')

        subjects_data = []
        total_obt = 0
        total_max = 0

        for m in marks_qs:
            subjects_data.append({
                "subject": m.subject.name,
                "marks_obtained": float(m.marks_obtained),
                "total_marks": float(m.total_marks),
                "grade": m.grade_point
            })
            total_obt += float(m.marks_obtained)
            total_max += float(m.total_marks)

        percentage = (total_obt / total_max * 100) if total_max > 0 else 0

        return Response({
            "student_id": enrollment.student.student_id,
            "name": enrollment.student.student_name,
            "class": f"{enrollment.section.standard.name}-{enrollment.section.name}",
            "exam_type": f"{exam_obj.name} ({exam_obj.term.name})",
            "summary": {"total_obtained": total_obt, "percentage": f"{round(percentage, 1)}%"},
            "marks": subjects_data
        }, 200)

# ==========================================
# 8. ADMIN: EXAM SCHEDULE (PARTIAL TIME-TRAVEL)
# ==========================================
class AdminExamScheduleView(APIView):
    permission_classes = [IsAuthenticated, IsOwnerAdminOrAdminStaff]

    def post(self, request):
        try:
            active_year = AcademicYear.objects.get(is_current=True)
        except AcademicYear.DoesNotExist:
            return Response({"error": "No Active Academic Year"}, 500)

        data_list = request.data if isinstance(request.data, list) else [request.data]
        created_count = 0
        errors = []

        for entry in data_list:
            try:
                # --- VALIDATION BLOCK ---
                start_date = entry.get('start_date')
                end_date = entry.get('end_date')
                e_name = entry.get('exam_type')
                t_name = entry.get('term')

                # 1. Check Basics
                if not e_name or not t_name: 
                    errors.append("Exam Type or Term missing")
                    continue
                
                # 2. CHECK: Start Date > End Date
                if start_date and end_date:
                    if str(start_date) > str(end_date):
                        errors.append(f"Exam '{e_name}' skipped: Start Date ({start_date}) cannot be after End Date ({end_date}).")
                        continue

                # 3. CHECK: Active Year Scope
                if start_date and end_date and hasattr(active_year, 'start_date'):
                    if str(start_date) < str(active_year.start_date) or str(end_date) > str(active_year.end_date):
                        errors.append(
                            f"Exam '{e_name}' skipped: Dates ({start_date} to {end_date}) are outside the Active Year ({active_year.start_date} to {active_year.end_date})."
                        )
                        continue 
                # -------------------------

                classes = entry.get('class_names', []) 
                subjects_data = entry.get('subjects', [])

                # NEW: Added academic_year to enforce Time-Travel boundary
                term_obj, _ = ExamTerm.objects.get_or_create(
                    name__iexact=t_name, 
                    academic_year=active_year, 
                    defaults={'name': t_name}
                )
                exam_obj, _ = ExamType.objects.get_or_create(
                    name__iexact=e_name, 
                    term=term_obj, 
                    defaults={'name': e_name}
                )

                for cls_name in classes:
                    try:
                        std_obj = Standard.objects.get(name=cls_name)
                    except Standard.DoesNotExist:
                        errors.append(f"Class {cls_name} not found")
                        continue

                    schedule, _ = ExamSchedule.objects.get_or_create(
                        exam_type=exam_obj,
                        academic_year=active_year,
                        defaults={'start_date': entry['start_date'], 'end_date': entry['end_date']}
                    )
                    schedule.classes.add(std_obj)

                    for sub_entry in subjects_data:
                        ExamDetail.objects.update_or_create(
                            schedule=schedule,
                            subject_name=sub_entry['subject_name'],
                            defaults={
                                'exam_date': sub_entry['exam_date'],
                                'duration': sub_entry.get('duration', '3 hours'),
                                'session': sub_entry.get('session', 'FN')
                            }
                        )
                created_count += 1

            except Exception as e:
                errors.append(str(e))

        if created_count > 0:
            return Response({"message": f"Processed {created_count}", "errors": errors}, 201)
        else:
            return Response({"message": "No schedules created", "errors": errors}, 400)
            
    # GET: Updated for Time Travel
    def get(self, request):
        # 1. Use Helper
        active_year = get_academic_year(request)
        if not active_year: return Response({"error": "No Active Year"}, 500)
        
        exam_name = request.query_params.get('exam_type') 
        term_name = request.query_params.get('term')
        class_name = request.query_params.get('class')
        
        # Filter by the SELECTED year
        schedules = ExamSchedule.objects.filter(academic_year=active_year).order_by('-created_at')

        if exam_name and not term_name:
            return Response({"error": "Term required"}, 400)

        if exam_name: schedules = schedules.filter(exam_type__name__iexact=exam_name)
        if term_name: schedules = schedules.filter(exam_type__term__name__iexact=term_name)
        if class_name: schedules = schedules.filter(classes__name=class_name)
            
        context = {'filter_class': class_name} if class_name else {}
        serializer = ExamScheduleSerializer(schedules, many=True, context=context)
        return Response({"data": serializer.data, "academic_year": active_year.name}, 200)

    # PUT: Smart Put
    def put(self, request):
        schedule_id = request.data.get('schedule_id')
        subject_name = request.data.get('subject_name')
        target_class = request.data.get('class_name') 
        new_date = request.data.get('exam_date')
        new_session = request.data.get('session')
        new_duration = request.data.get('duration')

        if not schedule_id: return Response({"error": "schedule_id required"}, 400)
        schedule = get_object_or_404(ExamSchedule, id=schedule_id)

        # NEW: Schedule Edit Protection (Blocks Admin if Marks exist)
        if subject_name:
            if StudentMark.objects.filter(schedule=schedule, subject__name__iexact=subject_name).exists():
                return Response({"error": f"Cannot edit '{subject_name}'. Marks are already uploaded."}, 400)
        else:
            if StudentMark.objects.filter(schedule=schedule).exists():
                return Response({"error": "Cannot edit schedule header. Marks are already uploaded for this schedule."}, 400)

        # A. Header Edit
        if not subject_name:
            serializer = ExamScheduleSerializer(schedule, data=request.data, partial=True)
            if serializer.is_valid():
                serializer.save()
                return Response({"message": "Header updated", "data": serializer.data})
            return Response(serializer.errors, 400)

        # B. Split Logic
        if target_class:
            try:
                std_obj = Standard.objects.get(name=target_class)
            except Standard.DoesNotExist:
                return Response({"error": f"Class '{target_class}' not found"}, 404)

            if std_obj not in schedule.classes.all():
                return Response({"error": "Mismatch: Class not in this schedule"}, 400)

            if schedule.classes.count() > 1:
                schedule.classes.remove(std_obj)
                new_schedule = ExamSchedule.objects.create(
                    exam_type=schedule.exam_type,
                    academic_year=schedule.academic_year,
                    start_date=schedule.start_date,
                    end_date=schedule.end_date
                )
                new_schedule.classes.add(std_obj)
                
                target_detail = None
                for det in schedule.details.all():
                    new_det = ExamDetail.objects.create(
                        schedule=new_schedule,
                        subject_name=det.subject_name,
                        exam_date=det.exam_date,
                        duration=det.duration,
                        session=det.session
                    )
                    if det.subject_name.lower() == subject_name.lower():
                        target_detail = new_det
                
                if target_detail:
                    if new_date: target_detail.exam_date = new_date
                    if new_session: target_detail.session = new_session
                    if new_duration: target_detail.duration = new_duration
                    target_detail.save()
                    return Response({"message": "Schedule split!", "new_schedule_id": new_schedule.id})

        # C. Normal Edit
        detail = get_object_or_404(ExamDetail, schedule=schedule, subject_name__iexact=subject_name)
        if new_date: detail.exam_date = new_date
        if new_session: detail.session = new_session
        if new_duration: detail.duration = new_duration
        detail.save()

        return Response({"message": "Subject updated", "data": {"id": detail.id}})

    # DELETE: Smart Delete
    def delete(self, request):
        schedule_id = request.query_params.get('schedule_id')
        delete_type = request.query_params.get('type')
        exam_name = request.query_params.get('exam_type')
        term_name = request.query_params.get('term')
        subject_name = request.query_params.get('subject')
        class_name = request.query_params.get('class') or request.query_params.get('class_name')

        if schedule_id:
            try:
                schedule = ExamSchedule.objects.get(id=schedule_id)
                if delete_type == 'class':
                    # NEW: Protection
                    if StudentMark.objects.filter(schedule=schedule, enrollment__section__standard__name=class_name).exists():
                        return Response({"error": f"Cannot remove class '{class_name}'. Marks are already uploaded."}, 400)
                    try:
                        std = Standard.objects.get(name=class_name)
                        if std in schedule.classes.all():
                            schedule.classes.remove(std)
                            if schedule.classes.count() == 0: schedule.delete()
                            return Response({"message": "Class removed."}, 200)
                        return Response({"error": "Class not in schedule"}, 400)
                    except Standard.DoesNotExist:
                        return Response({"error": "Class not found"}, 404)
                
                elif delete_type == 'subject':
                    s_name = request.query_params.get('subject_name') or subject_name
                    # NEW: Protection
                    if StudentMark.objects.filter(schedule=schedule, subject__name__iexact=s_name).exists():
                        return Response({"error": f"Cannot delete '{s_name}'. Marks are already uploaded."}, 400)
                    ExamDetail.objects.filter(schedule=schedule, subject_name__iexact=s_name).delete()
                    return Response({"message": "Subject deleted."}, 200)
                else:
                    # NEW: Protection
                    if StudentMark.objects.filter(schedule=schedule).exists():
                        return Response({"error": "Cannot delete schedule. Marks are already uploaded."}, 400)
                    schedule.delete()
                    return Response({"message": "Schedule deleted."}, 200)
            except ExamSchedule.DoesNotExist:
                return Response({"error": "Schedule ID not found"}, 404)

        # Bulk Delete (Restricted to Active Year for Safety)
        try:
            active_year = AcademicYear.objects.get(is_current=True)
        except AcademicYear.DoesNotExist:
            return Response({"error": "No Active Academic Year"}, 500)
            
        if not exam_name or not term_name:
             return Response({"error": "Exam/Term required for bulk delete"}, 400)

        schedules = ExamSchedule.objects.filter(
            exam_type__name__iexact=exam_name,
            exam_type__term__name__iexact=term_name,
            academic_year=active_year
        )

        if not schedules.exists(): return Response({"error": "No schedules found"}, 404)

        # NEW: Bulk Delete Protection
        if subject_name:
            if StudentMark.objects.filter(schedule__in=schedules, subject__name__iexact=subject_name).exists():
                return Response({"error": f"Cannot bulk remove '{subject_name}'. Marks exist in one or more schedules."}, 400)
        elif class_name:
            if StudentMark.objects.filter(schedule__in=schedules, enrollment__section__standard__name=class_name).exists():
                return Response({"error": f"Cannot bulk remove class '{class_name}'. Marks exist in one or more schedules."}, 400)
        else:
            if StudentMark.objects.filter(schedule__in=schedules).exists():
                return Response({"error": "Cannot bulk delete schedules. Marks exist in one or more schedules."}, 400)

        if subject_name:
            if class_name:
                try:
                    target_std = Standard.objects.get(name=class_name)
                    target_schedules = schedules.filter(classes=target_std)
                    split_count = 0
                    for sch in target_schedules:
                        if sch.classes.count() > 1:
                            sch.classes.remove(target_std)
                            new_sch = ExamSchedule.objects.create(
                                exam_type=sch.exam_type, academic_year=sch.academic_year,
                                start_date=sch.start_date, end_date=sch.end_date
                            )
                            new_sch.classes.add(target_std)
                            for det in sch.details.all():
                                if det.subject_name.lower() != subject_name.lower():
                                    ExamDetail.objects.create(
                                        schedule=new_sch, subject_name=det.subject_name,
                                        exam_date=det.exam_date, duration=det.duration, session=det.session
                                    )
                            split_count += 1
                        else:
                            ExamDetail.objects.filter(schedule=sch, subject_name__iexact=subject_name).delete()
                            split_count += 1
                    return Response({"message": f"Subject removed (Split {split_count})."}, 200)
                except Standard.DoesNotExist:
                     return Response({"error": "Class not found"}, 404)
            else:
                count, _ = ExamDetail.objects.filter(schedule__in=schedules, subject_name__iexact=subject_name).delete()
                return Response({"message": f"Deleted subject from {count} schedules."}, 200)

        if class_name:
            try:
                std = Standard.objects.get(name=class_name)
                count = 0
                for sch in schedules:
                    if std in sch.classes.all():
                        sch.classes.remove(std)
                        count += 1
                        if sch.classes.count() == 0: sch.delete()
                return Response({"message": f"Removed Class {class_name} from {count} schedules."}, 200)
            except Standard.DoesNotExist:
                return Response({"error": "Class not found"}, 404)

        count, _ = schedules.delete()
        return Response({"message": f"Deleted {count} schedules."}, 200)

class AdminExamTermScheduleView(APIView):
    """
    GET: Terms with nested exams and schedules for admin overview.
    Keeps existing terms/schedule APIs unchanged.
    """
    permission_classes = [IsAuthenticated, IsOwnerAdminOrAdminStaff]

    def get(self, request):
        active_year = get_academic_year(request)
        if not active_year:
            return Response({"error": "No Active Year"}, 500)

        schedules = (
            ExamSchedule.objects
            .filter(academic_year=active_year)
            .select_related('exam_type', 'exam_type__term')
            .prefetch_related('classes', 'details')
            .order_by('exam_type__term__rank', 'exam_type__rank', '-created_at')
        )

        schedule_map = defaultdict(list)
        today = timezone.now().date()

        for schedule in schedules:
            if schedule.start_date > today:
                status_label = "upcoming"
            elif schedule.start_date <= today <= schedule.end_date:
                status_label = "ongoing"
            else:
                status_label = "finished"

            schedule_map[schedule.exam_type_id].append({
                "id": schedule.id,
                "exam_type_name": schedule.exam_type.name,
                "start_date": schedule.start_date,
                "end_date": schedule.end_date,
                "created_at": schedule.created_at,
                "status": status_label,
                "classes": [std.name for std in schedule.classes.all()],
                "subjects": [
                    {
                        "id": detail.id,
                        "subject_name": detail.subject_name,
                        "exam_date": detail.exam_date,
                        "duration": detail.duration,
                        "session": detail.session
                    }
                    for detail in schedule.details.all().order_by('exam_date')
                ]
            })

        terms = (
            ExamTerm.objects
            .filter(academic_year=active_year)
            .prefetch_related('exams')
            .order_by('rank')
        )

        response_data = []
        for term in terms:
            exam_entries = []
            total_schedules = 0

            for exam in term.exams.all().order_by('rank'):
                nested_schedules = schedule_map.get(exam.id, [])
                total_schedules += len(nested_schedules)
                exam_entries.append({
                    "id": exam.id,
                    "name": exam.name,
                    "rank": exam.rank,
                    "max_marks": str(exam.max_marks),
                    "schedules": nested_schedules
                })

            response_data.append({
                "term_id": term.id,
                "term_name": term.name,
                "term_rank": term.rank,
                "total_exams": len(exam_entries),
                "total_schedules": total_schedules,
                "exams": exam_entries
            })

        return Response({
            "academic_year": active_year.name,
            "data": response_data
        }, 200)

# ==========================================
# 9. STUDENT DASHBOARD (YEAR-SAFE)
# ==========================================
class StudentExamDashboardView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        try:
            active_year = AcademicYear.objects.get(is_current=True) # <--- LOCK
        except AcademicYear.DoesNotExist:
            return Response({"error": "No Active Academic Year"}, 500)

        try:
            student = request.user.student_profile
            enrollment = Enrollment.objects.get(student=student, academic_year=active_year)
            my_standard = enrollment.section.standard
        except (AttributeError, Enrollment.DoesNotExist):
            return Response({"error": "Access Denied or Not Enrolled"}, 403)

        schedules = ExamSchedule.objects.filter(
            classes=my_standard, 
            academic_year=active_year
        ).select_related('exam_type__term').order_by('start_date')
        
        today = timezone.now().date()
        # Changed from lists [] to dicts {} to allow grouping by Term
        dashboard = {"ongoing": {}, "upcoming": {}, "completed": {}}

        for s in schedules:
            term_name = s.exam_type.term.name
            summary = {
                "schedule_id": s.id,
                "exam_type": s.exam_type.name,
                "term": term_name,
                "start_date": s.start_date,
                "end_date": s.end_date,
                "status": "Ongoing" if s.start_date <= today <= s.end_date else "Upcoming" if s.start_date > today else "Completed"
            }
            
            # Use setdefault to create the Term key dynamically, then append the exam
            if s.start_date <= today <= s.end_date: 
                dashboard["ongoing"].setdefault(term_name, []).append(summary)
            elif s.start_date > today: 
                dashboard["upcoming"].setdefault(term_name, []).append(summary)
            else: 
                dashboard["completed"].setdefault(term_name, []).append(summary)

        return Response({"student": student.student_name, "data": dashboard})

class StudentExamTimetableView(APIView):
    permission_classes = [IsAuthenticated]
    def get(self, request):
        # 1. Lock to Active Year
        try:
            active_year = AcademicYear.objects.get(is_current=True)
        except AcademicYear.DoesNotExist:
            return Response({"error": "No Active Academic Year"}, 500)

        # 2. Get Student's Enrollment for this year
        try:
            student = request.user.student_profile
            enrollment = Enrollment.objects.get(student=student, academic_year=active_year)
        except (AttributeError, Enrollment.DoesNotExist):
            return Response({"error": "Access Denied or Not Enrolled"}, 403)

        schedule_id = request.query_params.get('schedule_id')
        
        # 3. STRICT IDOR PROTECTION
        schedule = get_object_or_404(
            ExamSchedule, 
            id=schedule_id, 
            academic_year=active_year,
            classes=enrollment.section.standard
        )
        details = ExamDetail.objects.filter(schedule=schedule).order_by('exam_date')
        timetable = [{"subject": d.subject_name, "date": d.exam_date, "session": d.session, "duration": d.duration} for d in details]
        return Response({"exam_type": schedule.exam_type.name, "dates": f"{schedule.start_date} to {schedule.end_date}", "timetable": timetable})

# ==========================================
# 10. DYNAMIC PERFORMANCE DASHBOARD (LOGIC RESTORED & YEAR-SAFE)
# ==========================================
class StudentPerformanceDashboardView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        try:
            active_year = AcademicYear.objects.get(is_current=True) # <--- LOCK
        except AcademicYear.DoesNotExist:
            return Response({"error": "No Active Academic Year"}, 500)

        try:
            student = request.user.student_profile
            # Use Enrollment
            enrollment = Enrollment.objects.get(student=student, academic_year=active_year)
        except (AttributeError, Enrollment.DoesNotExist):
            return Response({"error": "Access Denied"}, 403)

        req_exam_type = request.query_params.get('exam_type')
        req_subject = request.query_params.get('subject')
        term_name = request.query_params.get('term')        
        compare_str = request.query_params.get('compare') 

        # MODE 1: EXAM BREAKDOWN
        if req_exam_type:
            try:
                if term_name:
                    exam_obj = ExamType.objects.get(name__iexact=req_exam_type, term__name__iexact=term_name, term__academic_year=active_year)
                else:
                    exam_obj = ExamType.objects.filter(name__iexact=req_exam_type, term__academic_year=active_year).first()
                    if not exam_obj: raise ExamType.DoesNotExist
            except ExamType.DoesNotExist:
                return Response({"error": f"Exam '{req_exam_type}' not found"}, 404)

            all_subjects = Subject.objects.filter(standard=enrollment.section.standard)
            data = []

            for sub in all_subjects:
                try:
                    # QUERY BY ENROLLMENT
                    m = StudentMark.objects.get(enrollment=enrollment, subject=sub, schedule__exam_type=exam_obj)
                    data.append({
                        "subject": sub.name,
                        "mark": float(m.marks_obtained),
                        "max_mark": float(m.total_marks),
                        "grade": m.grade_point
                    })
                except StudentMark.DoesNotExist:
                    # FIX: Use actual exam max marks instead of hardcoded 100
                    data.append({
                        "subject": sub.name, 
                        "mark": None, 
                        "max_mark": float(exam_obj.max_marks), 
                        "grade": "-"
                    })

            return Response({
                "student": student.student_name,
                "view": "Exam Breakdown",
                "exam": f"{exam_obj.name} ({exam_obj.term.name})",
                "breakdown": data
            })

        # MODE 2: TREND GRAPHS
        all_exams = ExamType.objects.filter(term__academic_year=active_year).select_related('term').order_by('term__rank', 'rank')
        if term_name: all_exams = all_exams.filter(term__name__iexact=term_name)
        
        if compare_str:
            items = [x.strip() for x in compare_str.split(',')]
            query = Q()
            for item in items:
                if ":" in item:
                    n, t = item.split(':')
                    query |= Q(name__iexact=n.strip(), term__name__iexact=t.strip())
                else:
                    query |= Q(name__iexact=item)
            all_exams = all_exams.filter(query)

        graph_data = []
        previous_val = None

        for exam in all_exams:
            label = f"{exam.name} ({exam.term.name})"
            
            # QUERY BY ENROLLMENT
            if req_subject:
                marks = StudentMark.objects.filter(enrollment=enrollment, schedule__exam_type=exam, subject__name__iexact=req_subject).first()
                # FIX: If no marks found, we set values to 0 to trigger Neutral logic
                current_val = float(marks.marks_obtained) if marks else 0
                max_val = float(marks.total_marks) if marks else float(exam.max_marks)
            else:
                marks_qs = StudentMark.objects.filter(enrollment=enrollment, schedule__exam_type=exam)
                if marks_qs.exists():
                    current_val = sum([float(m.marks_obtained) for m in marks_qs])
                    max_val = sum([float(m.total_marks) for m in marks_qs])
                else:
                    current_val, max_val = 0, 1

            percentage = round((current_val / max_val) * 100, 1) if max_val > 0 else 0
            
            # --- TREND LOGIC FIX ---
            trend = "neutral"
            change_pct = 0
            
            # FIX: Added 'and percentage > 0' check.
            # This ensures if current exam is 0% (missing), we DO NOT calculate a drop.
            if previous_val is not None and previous_val > 0 and percentage > 0:
                diff = percentage - previous_val
                change_pct = round((diff / previous_val) * 100, 2)
                if diff > 0: trend = "increase"
                elif diff < 0: trend = "decrease"
            
            # Only update previous_val if this exam had real data
            if percentage > 0: previous_val = percentage

            graph_data.append({
                "exam": label,
                "value": percentage,
                "change_percentage": change_pct,
                "trend": trend
            })

        return Response({
            "student": student.student_name,
            "view": f"Trend Analysis - {req_subject if req_subject else 'Overall'}",
            "graph_data": graph_data
        })

# ==========================================
# 12. TEACHER SCHEDULED EXAMS (TIMETABLE-SECURED)
# ==========================================
class TeacherScheduledExamsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        try:
            active_year = AcademicYear.objects.get(is_current=True) # <--- LOCK
        except AcademicYear.DoesNotExist:
            return Response({"error": "No Active Academic Year"}, 500)

        teacher = request.user.teacher_profile
        filter_type = request.query_params.get('filter')

        # SCENARIO 1: CLASS TEACHER VIEW (Grouped)
        if filter_type == 'my_class':
            try:
                # --- CORRECTED CLASS TEACHER LOOKUP ---
                ct = ClassTeacher.objects.get(teacher=teacher, academic_year=active_year)
                my_section = ct.section
            except ClassTeacher.DoesNotExist:
                return Response({"error": "You are not assigned as a Class Teacher."}, 403)

            schedules = ExamSchedule.objects.filter(
                classes=my_section.standard,
                academic_year=active_year # <--- LOCK
            ).distinct().prefetch_related('details', 'exam_type__term')

            grouped_response = []
            for sch in schedules:
                timetable_entries = []
                for detail in sch.details.all():
                    timetable_entries.append({
                        "subject": detail.subject_name,
                        "date": detail.exam_date,
                        "session": detail.session,
                        "duration": detail.duration
                    })
                timetable_entries.sort(key=lambda x: x['date'])
                
                grouped_response.append({
                    "exam_type": sch.exam_type.name,
                    "term": sch.exam_type.term.name,
                    "start_date": sch.start_date,
                    "end_date": sch.end_date,
                    "timetable": timetable_entries
                })

            return Response({"status": "success", "view_type": "Class Teacher", "data": grouped_response})

        # SCENARIO 2: SUBJECT TEACHER VIEW (Flat List with Timetable Source)
        # We need to find ALL sections where this teacher has a slot in the Active Year
        
        # 1. Fetch all Timetable Slots for this teacher in Active Year
        slots = TimetableSlot.objects.filter(
            teacher=teacher,
            academic_year=active_year
        ).select_related('section', 'subject', 'section__standard')

        # 2. Build a map: Standard ID -> { SubjectName -> [SectionName1, SectionName2] }
        teacher_map = defaultdict(lambda: defaultdict(set))
        
        for slot in slots:
            std_id = slot.section.standard.id
            sub_name = slot.subject.name.lower()
            sec_name = slot.section.name
            teacher_map[std_id][sub_name].add(sec_name)

        # 3. Fetch Exam Schedules relevant to these Standards
        schedules = ExamSchedule.objects.filter(
            classes__id__in=list(teacher_map.keys()),
            academic_year=active_year
        ).distinct().prefetch_related('details', 'classes', 'exam_type__term')

        response_data = []
        for sch in schedules:
            linked_standards = sch.classes.all()
            for std in linked_standards:
                if std.id in teacher_map:
                    for detail in sch.details.all():
                        exam_subject = detail.subject_name.lower()
                        # Check if teacher teaches THIS subject to THIS standard
                        if exam_subject in teacher_map[std.id]:
                            # Get the specific sections they teach
                            my_sections = sorted(list(teacher_map[std.id][exam_subject]))
                            
                            response_data.append({
                                "exam_type": sch.exam_type.name,
                                "term": sch.exam_type.term.name,
                                "subject": detail.subject_name,
                                "exam_date": detail.exam_date,
                                "session": detail.session,
                                "duration": detail.duration,
                                "standard": std.name,
                                "sections": my_sections,
                                "display_text": f"{std.name} - {', '.join(my_sections)}"
                            })
        
        response_data.sort(key=lambda x: x['exam_date'])
        return Response({"status": "success", "view_type": "Subject Teacher", "scheduled_exams": response_data})

# ==========================================
# 13. ADMIN APPROVAL VIEW (YEAR-SAFE & RESTRICTED)
# ==========================================
class AdminMarkApprovalView(APIView):
    permission_classes = [IsAuthenticated, IsOwnerAdminOrAdminStaff] 

    def get(self, request):
        try:
            active_year = AcademicYear.objects.get(is_current=True)
        except AcademicYear.DoesNotExist:
            return Response({"error": "No Active Academic Year"}, 500)

        # FIX: Filter requests by the Active Academic Year only
        pending_reqs = MarkChangeRequest.objects.filter(
            status='PENDING',
            enrollment__academic_year=active_year # <--- CRITICAL SECURITY FIX
        ).select_related(
            'enrollment__student', 'enrollment__section', 'schedule__exam_type__term', 'subject', 'requested_by'
        )
        
        data = []
        for req in pending_reqs:
            data.append({
                "request_id": req.id,
                "student": f"{req.enrollment.student.student_name} ({req.enrollment.student.student_id})",
                "class": f"{req.enrollment.section.standard.name}-{req.enrollment.section.name}",
                "term": req.schedule.exam_type.term.name, # <--- CHANGED
                "exam": req.schedule.exam_type.name,      # <--- CHANGED
                "subject": req.subject.name,
                "old_marks": req.old_marks,
                "new_marks": req.new_marks,
                "teacher": req.requested_by.name,
                "reason": req.reason,
                "date": req.created_at.date()
            })
            
        return Response({"pending_approvals": data})

    def post(self, request):
        try:
            active_year = AcademicYear.objects.get(is_current=True)
        except AcademicYear.DoesNotExist:
            return Response({"error": "No Active Academic Year"}, 500)

        req_id = request.data.get('request_id')
        action = request.data.get('action') 
        
        try:
            # FIX: Ensure we only process requests for the current year
            req_obj = MarkChangeRequest.objects.get(
                id=req_id, 
                status='PENDING',
                enrollment__academic_year=active_year # <--- PREVENTS MODIFYING OLD YEARS
            )
        except MarkChangeRequest.DoesNotExist:
            return Response({"error": "Request not found, already processed, or belongs to a past year."}, 404)

        if action == 'REJECT':
            req_obj.status = 'REJECTED'
            req_obj.save()

            # --- NOTIFY TEACHER (REJECTION) ---
            try:
                if req_obj.requested_by.user:
                    send_notification_to_users(
                        users=[req_obj.requested_by.user],
                        sender=request.user,
                        title="Mark Request Rejected",
                        message=f"Your request to change marks for {req_obj.enrollment.student.student_name} was rejected.",
                        notif_type="Mark Update"
                    )
            except Exception as e:
                print(f"Notif Error: {e}")
            # ----------------------------------

            return Response({"message": "Request Rejected"}, 200)

        elif action == 'APPROVE':
            try:
                # Find the Mark Entry using Enrollment
                real_mark_entry = StudentMark.objects.get(
                    enrollment=req_obj.enrollment, 
                    schedule=req_obj.schedule,
                    subject=req_obj.subject
                )
                real_mark_entry.marks_obtained = req_obj.new_marks
                real_mark_entry.save()
                
                req_obj.status = 'APPROVED'
                req_obj.save()
                whatsapp_ok, whatsapp_response = send_student_mark_whatsapp(
                    req_obj.enrollment,
                    req_obj.subject,
                    req_obj.schedule.exam_type,
                    req_obj.schedule.exam_type.term.name,
                    req_obj.new_marks,
                    real_mark_entry.total_marks,
                )
                whatsapp_error = str(whatsapp_response.get('error', '')) if isinstance(whatsapp_response, dict) else ''

                # --- NOTIFY TEACHER & STUDENT (APPROVAL) ---
                try:
                    # 1. Notify Teacher
                    if req_obj.requested_by.user:
                        send_notification_to_users(
                            users=[req_obj.requested_by.user],
                            sender=request.user,
                            title="Mark Request Approved",
                            message=f"Marks for {req_obj.enrollment.student.student_name} updated successfully.",
                            notif_type="Mark Update"
                        )
                    
                    # 2. Notify Student (Optional: If you want them to know immediately)
                    if req_obj.enrollment.student.user:
                        send_notification_to_users(
                            users=[req_obj.enrollment.student.user],
                            sender=request.user,
                            title="Marks Updated",
                            message=f"Your marks in {req_obj.subject.name} have been updated to {req_obj.new_marks}.",
                            notif_type="Exam Result"
                        )
                except Exception as e:
                    print(f"Notif Error: {e}")
                # -------------------------------------------

                return Response({
                    "message": "Approved. Marks updated.",
                    "whatsapp_sent": 1 if whatsapp_ok else 0,
                    "whatsapp_failed": 0 if whatsapp_ok or whatsapp_error.startswith('WhatsApp alert disabled:') or whatsapp_error == 'WhatsApp alerts are not configured.' or whatsapp_error == 'Father phone number is missing.' else 1,
                    "whatsapp_skipped": 0 if whatsapp_ok else 1 if whatsapp_error.startswith('WhatsApp alert disabled:') or whatsapp_error == 'WhatsApp alerts are not configured.' or whatsapp_error == 'Father phone number is missing.' else 0,
                    "whatsapp_error": whatsapp_error,
                }, 200)
            except StudentMark.DoesNotExist:
                return Response({"error": "Original mark entry missing"}, 500)

# goutham's new api asked 
class StudentAvailableExamsView(APIView):
    """
    Shows a simple list of exams scheduled for the student's CURRENT class
    in the CURRENT academic year, grouped by Term.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        # 1. LOCK: Get Current Active Year
        try:
            active_year = AcademicYear.objects.get(is_current=True)
        except AcademicYear.DoesNotExist:
            return Response({"error": "Configuration Error: No Active Academic Year found."}, 500)

        # 2. LOCK: Get Student's Class for THIS Active Year
        try:
            student = request.user.student_profile
            # STRICT LOGIC: Only looks for enrollment in the ACTIVE YEAR.
            # If student moved from 10th to 11th, this gets the 11th std enrollment.
            enrollment = Enrollment.objects.get(
                student=student, 
                academic_year=active_year, 
                is_active=True
            )
            current_standard = enrollment.section.standard
        except (AttributeError, Enrollment.DoesNotExist):
            return Response({"error": "You are not enrolled in the current academic year."}, 404)

        # 3. FILTER: Get Schedules for THIS Class and THIS Year
        schedules = ExamSchedule.objects.filter(
            classes=current_standard,       # Filter by specific class (e.g., 11th)
            academic_year=active_year       # Filter by specific year (e.g., 2025-26)
        ).select_related('exam_type', 'exam_type__term')

        # 4. FILTER: Optional Term Filter (e.g., ?term=Term 1)
        req_term = request.query_params.get('term')
        if req_term:
            schedules = schedules.filter(exam_type__term__name__iexact=req_term)

        # 5. GROUPING LOGIC
        # Structure: { "Term 1": ["Quarterly", "Unit Test 1"], "Term 2": [...] }
        grouped_data = defaultdict(list)
        grouped_details = defaultdict(dict)
        
        # Sort by Term Rank then Exam Rank to keep order (e.g., Term 1 before Term 2)
        sorted_schedules = schedules.order_by('exam_type__term__rank', 'exam_type__rank')

        for sch in sorted_schedules:
            term_name = sch.exam_type.term.name
            exam_name = sch.exam_type.name
            
            # Prevent duplicates if multiple schedules exist for same exam type
            if exam_name not in grouped_data[term_name]:
                grouped_data[term_name].append(exam_name)

        # 6. RESPONSE FORMAT
        response_data = []
        for term, exams in grouped_data.items():
            response_data.append({
                "term": term,
                "exams": exams
            })

        return Response({
            "status": "success",
            "academic_year": active_year.name,
            "enrolled_class": current_standard.name, # Confirms which class data is shown
            "data": response_data
        }, 200)


class TeacherAvailableExamsView(APIView):
    """
    Shows a simple list of exams scheduled for a specific STANDARD (Class)
    in the CURRENT academic year.
    
    Usage: GET /api/exams/teacher/available-exams/?class=10th Standard
    """
    permission_classes = [IsAuthenticated, IsTeacher]

    def get(self, request):
        # 1. LOCK: Get Current Active Year
        try:
            active_year = AcademicYear.objects.get(is_current=True)
        except AcademicYear.DoesNotExist:
            return Response({"error": "Configuration Error: No Active Academic Year found."}, 500)

        # 2. INPUTS: Teacher must specify which Standard (Class) they want to see
        req_class = request.query_params.get('class') # e.g. "10th Standard"
        req_term = request.query_params.get('term')   # Optional

        if not req_class:
            return Response({"error": "Parameter 'class' is required."}, 400)

        teacher = request.user.teacher_profile

        # 3. VERIFY: Does this Standard exist?
        try:
            target_standard = Standard.objects.get(name__iexact=req_class)
        except Standard.DoesNotExist:
            return Response({"error": f"Class '{req_class}' not found."}, 404)

        # 4. PERMISSION: Does this teacher teach ANY section in this Standard?
        # We don't need a specific section, but we should verify they have access to this Level.
        
        # Check A: Is Class Teacher for ANY section in this Standard?
        is_class_teacher = ClassTeacher.objects.filter(
            teacher=teacher, 
            academic_year=active_year, 
            section__standard=target_standard # <--- Checks all sections in 10th
        ).exists()

        # Check B: Is Subject Teacher for ANY section in this Standard?
        is_subject_teacher = False
        if not is_class_teacher:
            is_subject_teacher = TimetableSlot.objects.filter(
                teacher=teacher,
                academic_year=active_year,
                section__standard=target_standard # <--- Checks all sections in 10th
            ).exists()

        if not is_class_teacher and not is_subject_teacher:
            return Response({"error": f"Permission Denied: You do not teach any section in {target_standard.name}."}, 403)

        # 5. FILTER: Get Schedules for this Class and Year
        # Admin schedules exams for the Standard (10th), not per section.
        schedules = ExamSchedule.objects.filter(
            classes=target_standard,        # Filter by 10th Standard
            academic_year=active_year       # Filter by 2025-26
        ).select_related('exam_type', 'exam_type__term')

        # Optional Term Filter
        if req_term:
            schedules = schedules.filter(exam_type__term__name__iexact=req_term)

        # 6. GROUPING LOGIC
        grouped_data = defaultdict(list)
        grouped_details = defaultdict(dict)
        
        # Sort by Term Rank then Exam Rank
        sorted_schedules = schedules.order_by('exam_type__term__rank', 'exam_type__rank')

        for sch in sorted_schedules:
            term_name = sch.exam_type.term.name
            exam_name = sch.exam_type.name
            
            if exam_name not in grouped_data[term_name]:
                grouped_data[term_name].append(exam_name)

            exam_meta = grouped_details[term_name].setdefault(exam_name, {
                "exam": exam_name,
                "subjects": [],
                "earliest_exam_date": None,
                "latest_exam_date": None,
            })

            exam_dates = []
            for detail in sch.details.all().order_by('exam_date'):
                exam_date = detail.exam_date.isoformat() if detail.exam_date else None
                if exam_date:
                    exam_dates.append(exam_date)
                exam_meta["subjects"].append({
                    "subject": detail.subject_name,
                    "exam_date": exam_date,
                    "session": detail.session,
                    "duration": detail.duration,
                })

            if exam_dates:
                existing_dates = [
                    d for d in [
                        exam_meta["earliest_exam_date"],
                        exam_meta["latest_exam_date"],
                    ] if d
                ] + exam_dates
                exam_meta["earliest_exam_date"] = min(existing_dates)
                exam_meta["latest_exam_date"] = max(existing_dates)

        # 7. RESPONSE FORMAT
        response_data = []
        for term, exams in grouped_data.items():
            response_data.append({
                "term": term,
                "exams": exams,
                "exam_details": [
                    grouped_details[term][exam_name]
                    for exam_name in exams
                    if exam_name in grouped_details[term]
                ],
            })

        return Response({
            "status": "success",
            "academic_year": active_year.name,
            "viewing_class": target_standard.name,
            "data": response_data
        }, 200)


class StudentLatestExamComparisonView(APIView):
    """
    GET: specific dashboard view comparing the LATEST completed exam 
    with the PREVIOUS completed exam for every subject.
    
    Logic:
    1. Finds all exams where the student has marks.
    2. Sorts them by Rank (Academic Order).
    3. Picks the last two (Latest & Previous).
    4. Compares percentage for each subject.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        # 1. LOCK: Active Academic Year
        try:
            active_year = AcademicYear.objects.get(is_current=True)
        except AcademicYear.DoesNotExist:
            return Response({"error": "Configuration Error: No Active Academic Year."}, 500)

        # 2. LOCK: Student Enrollment
        try:
            student = request.user.student_profile
            enrollment = Enrollment.objects.get(student=student, academic_year=active_year)
        except (AttributeError, Enrollment.DoesNotExist):
            return Response({"error": "Access Denied: You are not enrolled in the active year."}, 403)

        # 3. FETCH: Get all exams where this student actually has marks
        # We group by ExamType to see which exams have been "taken" (have marks)
        taken_exam_ids = StudentMark.objects.filter(
            enrollment=enrollment
        ).values_list('schedule__exam_type', flat=True).distinct()
        
        if not taken_exam_ids:
            return Response({
                "student": student.student_name,
                "message": "No exams completed yet.",
                "data": []
            })

        # 4. SORT: Order these exams by Academic Rank (Term Rank -> Exam Rank)
        # This ensures we compare "Quarterly" with "Unit Test 1", not random dates.
        completed_exams = ExamType.objects.filter(
            id__in=taken_exam_ids
        ).select_related('term').order_by('term__rank', 'rank')

        count = completed_exams.count()

        # CASE A: Only 1 Exam Completed (Nothing to compare)
        if count == 1:
            latest_exam = completed_exams.first()
            return Response({
                "student": student.student_name,
                "status": "Single Exam",
                "latest_exam": f"{latest_exam.name} ({latest_exam.term.name})",
                "message": "First exam completed. No previous exam to compare with.",
                "data": [] 
            })

        # CASE B: Compare Latest vs Previous
        latest_exam = completed_exams.last()       # e.g., Half Yearly
        previous_exam = completed_exams[count-2]   # e.g., Quarterly

        # 5. SUBJECT COMPARISON LOGIC
        subjects = Subject.objects.filter(standard=enrollment.section.standard)
        comparison_data = []

        for sub in subjects:
            # Helper to get % for a specific exam & subject
            def get_percentage(exam_obj):
                try:
                    m = StudentMark.objects.get(enrollment=enrollment, schedule__exam_type=exam_obj, subject=sub)
                    # CORE LOGIC: Convert to Percentage for fair comparison
                    if m.total_marks > 0:
                        return round((float(m.marks_obtained) / float(m.total_marks)) * 100, 1)
                    return 0.0
                except StudentMark.DoesNotExist:
                    return None # Mark missing (Absent or not scheduled)

            # Get Percentages
            latest_pct = get_percentage(latest_exam)
            prev_pct = get_percentage(previous_exam)

            # Skip if student was absent for BOTH (No data to show)
            if latest_pct is None and prev_pct is None:
                continue

            # Handle missing data (Treat as 0 for comparison, or keep as Neutral)
            val_latest = latest_pct if latest_pct is not None else 0.0
            val_prev = prev_pct if prev_pct is not None else 0.0

            # Calculate Difference
            diff = round(val_latest - val_prev, 1)
            
            trend = "Neutral"
            if diff > 0: trend = "Increase"
            elif diff < 0: trend = "Decrease"

            # If previous exam was missed (0%), strict growth is technically infinite, 
            # but for dashboard we usually just show the new value as "Increase".
            if prev_pct is None or prev_pct == 0:
                if val_latest > 0: trend = "Increase" # 0 -> 80 is an increase
                else: trend = "Neutral" # 0 -> 0

            comparison_data.append({
                "subject": sub.name,
                "latest_exam": latest_exam.name,
                "latest_percentage": val_latest,
                "previous_exam": previous_exam.name,
                "previous_percentage": val_prev,
                "change": diff,
                "trend": trend
            })

        return Response({
            "student": student.student_name,
            "status": "Comparison",
            "latest_exam": f"{latest_exam.name} ({latest_exam.term.name})",
            "previous_exam": f"{previous_exam.name} ({previous_exam.term.name})",
            "data": comparison_data
        })

# ==========================================
# TEACHER: SIMPLE TERM DROPDOWN
# ==========================================
class TeacherTermDropdownView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        # 1. LOCK to Active Year
        try:
            active_year = AcademicYear.objects.get(is_current=True)
        except AcademicYear.DoesNotExist:
            return Response({"error": "No Active Academic Year"}, 500)

        # Fetch all terms for the active year ordered by their rank
        terms = ExamTerm.objects.filter(academic_year=active_year).order_by('rank')
        
        # specific simple format: ID and Name only
        data = [{"id": t.id, "name": t.name} for t in terms]
        
        return Response(data, 200)


### SIVA BRO EXAM VIEWS ### # # # # # # 

# ==========================================
# 11. SUBJECT MARKS LIST (ADMIN TIME-TRAVEL ENABLED)
# ==========================================
class SubjectMarksListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        # 1. Use Helper
        active_year = get_academic_year(request)
        if not active_year: return Response({"error": "No Active Year"}, 500)

        class_name = request.query_params.get('class')
        section_name = request.query_params.get('section')
        subject_name = request.query_params.get('subject')
        exam_name = request.query_params.get('exam_type')
        term_name = request.query_params.get('term')

        if not all([class_name, section_name, subject_name, exam_name]):
            return Response({"error": "Missing params"}, 400)

        # Permission Check
        is_authorized = False
        if IsOwnerAdminOrAdminStaff().has_permission(request, self):
            is_authorized = True
        elif hasattr(request.user, 'teacher_profile'):
            # --- CORRECTED ALLOCATION CHECK (TIMETABLE) ---
            try:
                target_section = Section.objects.get(standard__name=class_name, name=section_name)
                target_subject = Subject.objects.get(name__iexact=subject_name, standard__name=class_name)
                
                has_permission = TimetableSlot.objects.filter(
                    academic_year=active_year,
                    teacher=request.user.teacher_profile,
                    section=target_section,
                    subject=target_subject
                ).exists()
                if has_permission: is_authorized = True
            except:
                pass 

        if not is_authorized:
             return Response({"error": "Access Denied."}, status=403)

        try:
            if term_name:
                exam_obj = ExamType.objects.get(name__iexact=exam_name, term__name__iexact=term_name, term__academic_year=active_year)
            else:
                exam_obj = ExamType.objects.filter(name__iexact=exam_name, term__academic_year=active_year).first()
                if not exam_obj: raise ExamType.DoesNotExist
        except ExamType.DoesNotExist:
            return Response({"error": f"Exam '{exam_name}' not found"}, 404)

        # 3. Fetch Data via Enrollments (Year Aware)
        try:
            enrollments = Enrollment.objects.filter(
                section__standard__name=class_name,
                section__name=section_name,
                academic_year=active_year # <--- Uses Helper Year
            ).select_related('student')
        except:
            return Response({"error": "Data not found"}, 404)

        data_list = []
        for enroll in enrollments:
            try:
                mark_entry = StudentMark.objects.get(enrollment=enroll, subject__name__iexact=subject_name, schedule__exam_type=exam_obj)
                marks_val = mark_entry.marks_obtained
            except StudentMark.DoesNotExist:
                marks_val = None 

            data_list.append({
                "student_id": enroll.student.student_id,
                "name": enroll.student.student_name,
                "marks": marks_val
            })

        return Response({
            "status": 200,
            "class": f"{class_name}-{section_name}",
            "subject": subject_name,
            "exam": f"{exam_obj.name} ({exam_obj.term.name})",
            "academic_year": active_year.name,
            "data": data_list
        })


class SubjectMarksListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        # 1. Use Helper
        active_year = get_academic_year(request)
        if not active_year: return Response({"error": "No Active Year"}, 500)

        class_name = request.query_params.get('class')
        section_name = request.query_params.get('section')
        subject_name = request.query_params.get('subject')
        exam_name = request.query_params.get('exam_type')
        term_name = request.query_params.get('term')

        if not all([class_name, section_name, subject_name, exam_name]):
            return Response({"error": "Missing params"}, 400)

        # Permission Check
        is_authorized = False
        if IsOwnerAdminOrAdminStaff().has_permission(request, self):
            is_authorized = True
        elif hasattr(request.user, 'teacher_profile'):
            # --- CORRECTED ALLOCATION CHECK (TIMETABLE) ---
            try:
                target_section = Section.objects.get(standard__name=class_name, name=section_name)
                target_subject = Subject.objects.get(
                    name__iexact=subject_name, 
                    standard__name=class_name
                )
                
                has_permission = TimetableSlot.objects.filter(
                    academic_year=active_year,
                    teacher=request.user.teacher_profile,
                    section=target_section,
                    subject=target_subject
                ).exists()
                if has_permission: is_authorized = True
            except:
                pass 

        if not is_authorized:
             return Response({"error": "Access Denied."}, status=403)

        try:
            if term_name:
                exam_obj = ExamType.objects.get(name__iexact=exam_name, term__name__iexact=term_name, term__academic_year=active_year)
            else:
                exam_obj = ExamType.objects.filter(name__iexact=exam_name, term__academic_year=active_year).first()
                if not exam_obj: raise ExamType.DoesNotExist
        except ExamType.DoesNotExist:
            return Response({"error": f"Exam '{exam_name}' not found"}, 404)

        # 3. Fetch Data via Enrollments (Year Aware)
        try:
            enrollments = Enrollment.objects.filter(
                section__standard__name=class_name,
                section__name=section_name,
                academic_year=active_year # <--- Uses Helper Year
            ).select_related('student')
        except:
            return Response({"error": "Data not found"}, 404)

        data_list = []
        for enroll in enrollments:
            try:
                mark_entry = StudentMark.objects.get(enrollment=enroll, subject__name__iexact=subject_name, schedule__exam_type=exam_obj)
                marks_val = mark_entry.marks_obtained
            except StudentMark.DoesNotExist:
                marks_val = None 

            data_list.append({
                "student_id": enroll.student.student_id,
                "name": enroll.student.student_name,
                "marks": marks_val
            })

        return Response({
            "status": 200,
            "class": f"{class_name}-{section_name}",
            "subject": subject_name,
            "exam": f"{exam_obj.name} ({exam_obj.term.name})",
            "academic_year": active_year.name,
            "data": data_list
        })


class AdminExamOverviewView(APIView):
    """
    GET: Returns a comprehensive overview of exams for admin dashboard.
    - Scheduled exams count (total, by status: upcoming, ongoing, completed)
    - Mark upload status (total marks entered, pending subjects, completion percentage)
    - Recent activity summary
    - Top performers in each class
    - Academic year context
    """
    permission_classes = [IsAuthenticated, IsOwnerAdminOrAdminStaff]

    def get(self, request):
        try:
            # Get academic year context (Admin can view active or specific year)
            active_year = get_academic_year(request)
            if not active_year:
                return Response(
                    {"error": "No Academic Year configured. Please set up an academic year first."},
                    status=status.HTTP_404_NOT_FOUND
                )

            today = timezone.now().date()

            # ==========================================
            # 1. EXAM SCHEDULE OVERVIEW
            # ==========================================
            schedules = ExamSchedule.objects.filter(
                academic_year=active_year
            ).select_related('exam_type', 'exam_type__term').prefetch_related('classes', 'details')

            total_scheduled_exams = schedules.count()

            # Categorize exams by status
            upcoming_exams = schedules.filter(start_date__gt=today).count()
            ongoing_exams = schedules.filter(
                start_date__lte=today,
                end_date__gte=today
            ).count()
            completed_exams = schedules.filter(end_date__lt=today).count()

            # ==========================================
            # 2. EXAM TYPE DISTRIBUTION
            # ==========================================
            exam_types_by_term = defaultdict(lambda: defaultdict(int))
            term_breakdown = []

            for schedule in schedules:
                if not schedule.exam_type or not schedule.exam_type.term:
                    continue
                term_name = schedule.exam_type.term.name
                exam_name = schedule.exam_type.name
                exam_types_by_term[term_name][exam_name] += 1

            for term_name, exams in exam_types_by_term.items():
                term_breakdown.append({
                    "term": term_name,
                    "exams": [{"name": name, "count": count} for name, count in exams.items()],
                    "total": sum(exams.values())
                })

            # ==========================================
            # 3. MARKS UPLOAD STATUS
            # ==========================================
            total_possible_mark_entries = 0
            marks_uploaded_count = 0
            subjects_without_marks = []
            completed_schedules_for_marks = schedules.filter(end_date__lt=today)

            for schedule in completed_schedules_for_marks:
                # Get all subjects scheduled for this exam
                exam_subjects = schedule.details.values_list('subject_name', flat=True).distinct()
                
                for subject_name in exam_subjects:
                    try:
                        # For each class this exam is scheduled for
                        for std in schedule.classes.all():
                            try:
                                # Get the actual Subject object
                                subject_obj = Subject.objects.get(
                                    name__iexact=subject_name,
                                    standard=std
                                )
                                
                                # Get all students in this class (across all sections)
                                enrollments = Enrollment.objects.filter(
                                    standard=std,
                                    academic_year=active_year,
                                    is_active=True
                                )
                                
                                student_count = enrollments.count()
                                
                                # Count how many marks have been uploaded for this subject/exam
                                marks_uploaded = StudentMark.objects.filter(
                                    enrollment__in=enrollments,
                                    subject=subject_obj,
                                    schedule=schedule
                                ).count()
                                
                                # Add to totals
                                total_possible_mark_entries += student_count
                                marks_uploaded_count += marks_uploaded
                                
                                # Track if this subject-exam combination has no marks
                                if marks_uploaded == 0 and student_count > 0:
                                    subjects_without_marks.append({
                                        "exam": schedule.exam_type.name if schedule.exam_type else None,
                                        "term": schedule.exam_type.term.name if schedule.exam_type and schedule.exam_type.term else None,
                                        "class": std.name,
                                        "subject": subject_name,
                                        "student_count": student_count
                                    })
                                    
                            except Subject.DoesNotExist:
                                subjects_without_marks.append({
                                    "exam": schedule.exam_type.name if schedule.exam_type else None,
                                    "term": schedule.exam_type.term.name if schedule.exam_type and schedule.exam_type.term else None,
                                    "class": std.name,
                                    "subject": subject_name,
                                    "error": "Subject not configured in system",
                                    "student_count": 0
                                })
                                continue
                                
                    except Exception:
                        continue

            # Calculate completion percentage
            marks_completion_percentage = 0
            if total_possible_mark_entries > 0:
                marks_completion_percentage = round(
                    (marks_uploaded_count / total_possible_mark_entries) * 100, 1
                )

            # ==========================================
            # 4. RECENT ACTIVITY (Last 7 days)
            # ==========================================
            last_7_days = today - timezone.timedelta(days=7)
            
            # Recently scheduled exams (using created_at from ExamSchedule model)
            recent_schedules = ExamSchedule.objects.filter(
                academic_year=active_year,
                created_at__date__gte=last_7_days
            ).order_by('-created_at')[:5]
            
            recent_exams = []
            for s in recent_schedules:
                classes_list = list(s.classes.all()[:3])
                classes_str = ", ".join([c.name for c in classes_list])
                if s.classes.count() > 3:
                    classes_str += f" and {s.classes.count()-3} more"
                
                recent_exams.append({
                    "id": s.id,
                    "exam": f"{s.exam_type.name} ({s.exam_type.term.name})" if s.exam_type and s.exam_type.term else None,
                    "classes": classes_str,
                    "start_date": s.start_date,
                    "created_at": s.created_at.date()
                })

            # For recent mark uploads - we can't use created_at, so we'll show counts by exam
            # Get all marks for this academic year
            all_marks = StudentMark.objects.filter(
                enrollment__academic_year=active_year
            ).select_related('schedule__exam_type', 'subject', 'schedule__exam_type__term')

            # Group by exam and subject for recent activity (we don't have timestamps)
            uploads_by_exam = defaultdict(int)
            exam_subject_combos = set()

            for mark in all_marks:
                key = f"{mark.schedule.id}_{mark.subject.id}"
                uploads_by_exam[key] += 1
                exam_subject_combos.add(key)

            # Get the most recent 5 exam-subject combinations (by highest ID - approximate)
            formatted_recent_uploads = []
            recent_marks = StudentMark.objects.filter(
                enrollment__academic_year=active_year
            ).select_related('schedule__exam_type', 'subject', 'schedule__exam_type__term').order_by('-id')[:100]

            recent_combos_seen = set()
            for mark in recent_marks:
                key = f"{mark.schedule.id}_{mark.subject.id}"
                if key not in recent_combos_seen and len(formatted_recent_uploads) < 5:
                    recent_combos_seen.add(key)
                    formatted_recent_uploads.append({
                        "exam": (
                            f"{mark.schedule.exam_type.name} ({mark.schedule.exam_type.term.name})"
                            if mark.schedule and mark.schedule.exam_type and mark.schedule.exam_type.term else None
                        ),
                        "subject": mark.subject.name,
                        "marks_entered": uploads_by_exam.get(key, 0),
                        "last_updated": "Recently"
                    })

            # ==========================================
            # 5. PENDING APPROVALS COUNT
            # ==========================================
            pending_approvals_count = MarkChangeRequest.objects.filter(
                status='PENDING',
                enrollment__academic_year=active_year
            ).count()

            # ==========================================
            # 6. EXAMS WITHOUT ANY MARKS
            # ==========================================
            # Limit to top 10 most urgent (largest classes first)
            subjects_without_marks = [s for s in subjects_without_marks if 'error' not in s]
            subjects_without_marks.sort(
                key=lambda x: x.get('student_count', 0), 
                reverse=True
            )
            
            pending_mark_entry = []
            for item in subjects_without_marks[:10]:
                pending_mark_entry.append({
                    "exam": item["exam"],
                    "term": item["term"],
                    "class": item["class"],
                    "subject": item["subject"],
                    "students_pending": item.get("student_count", 0)
                })

            # ==========================================
            # 7. CLASS-WISE COMPLETION SUMMARY
            # ==========================================
            class_completion = []
            
            # Get all standards with scheduled exams
            standards_with_exams = Standard.objects.filter(
                exam_schedules__academic_year=active_year
            ).distinct()

            for std in standards_with_exams:
                # Get all students in this standard
                total_students = Enrollment.objects.filter(
                    standard=std,
                    academic_year=active_year,
                    is_active=True
                ).count()
                
                if total_students == 0:
                    continue
                
                # Get all exam schedules for this standard
                std_schedules = ExamSchedule.objects.filter(
                    classes=std,
                    academic_year=active_year
                )
                
                std_total_marks_possible = 0
                std_marks_uploaded = 0
                
                for schedule in std_schedules:
                    schedule_subjects = schedule.details.values_list('subject_name', flat=True).distinct()
                    for subject_name in schedule_subjects:
                        try:
                            subject_obj = Subject.objects.get(
                                name__iexact=subject_name,
                                standard=std
                            )
                            
                            uploaded = StudentMark.objects.filter(
                                enrollment__standard=std,
                                enrollment__academic_year=active_year,
                                subject=subject_obj,
                                schedule=schedule
                            ).count()
                            
                            # Each student should have one mark per subject
                            std_total_marks_possible += total_students
                            std_marks_uploaded += uploaded
                            
                        except Subject.DoesNotExist:
                            continue
                
                completion_pct = 0
                if std_total_marks_possible > 0:
                    completion_pct = round((std_marks_uploaded / std_total_marks_possible) * 100, 1)
                
                class_completion.append({
                    "class": std.name,
                    "total_students": total_students,
                    "marks_uploaded": std_marks_uploaded,
                    "marks_expected": std_total_marks_possible,
                    "completion_percentage": completion_pct
                })

            # Sort by completion percentage (lowest first - needs attention)
            class_completion.sort(key=lambda x: x["completion_percentage"])

            # ==========================================
            # 8. TOP PERFORMERS BY CLASS
            # ==========================================
            top_performers_by_class = []
            
            # Get all classes that have exams
            classes_with_exams = Standard.objects.filter(
                exam_schedules__academic_year=active_year
            ).distinct().order_by('name')
            
            for std in classes_with_exams:
                # Get all completed exams for this class
                completed_std_schedules = ExamSchedule.objects.filter(
                    classes=std,
                    academic_year=active_year,
                    end_date__lt=today
                )
                
                if not completed_std_schedules.exists():
                    continue
                
                # Get all enrollments for this class
                enrollments = Enrollment.objects.filter(
                    standard=std,
                    academic_year=active_year,
                    is_active=True
                ).select_related('student')
                
                student_performance = []
                
                for enrollment in enrollments:
                    total_obtained = 0
                    total_max = 0
                    exams_attempted = 0
                    
                    # Get all marks for this student across completed exams
                    for schedule in completed_std_schedules:
                        marks = StudentMark.objects.filter(
                            enrollment=enrollment,
                            schedule=schedule
                        )
                        
                        for mark in marks:
                            total_obtained += float(mark.marks_obtained)
                            total_max += float(mark.total_marks)
                            exams_attempted += 1
                    
                    if total_max > 0 and exams_attempted > 0:
                        percentage = (total_obtained / total_max) * 100
                        student_performance.append({
                            "student_id": enrollment.student.student_id,
                            "student_name": enrollment.student.student_name,
                            "section": enrollment.section.name if enrollment.section else "N/A",
                            "total_marks": round(total_obtained, 1),
                            "percentage": round(percentage, 1),
                            "exams_attempted": exams_attempted,
                            "grade": get_grade(percentage)
                        })
                
                # Sort by percentage (highest first) and get top 3
                student_performance.sort(key=lambda x: x["percentage"], reverse=True)
                top_students = student_performance[:3]
                
                # Also get subject-wise toppers
                subject_toppers = []
                
                # Get unique subjects for this class
                class_subjects = Subject.objects.filter(standard=std)
                
                for subject in class_subjects[:3]:  # Limit to 3 subjects
                    subject_marks = []
                    
                    for enrollment in enrollments:
                        # Get the latest completed exam for this subject
                        mark = StudentMark.objects.filter(
                            enrollment=enrollment,
                            subject=subject,
                            schedule__in=completed_std_schedules
                        ).order_by('-id').first()
                        
                        if mark:
                            subject_marks.append({
                                "enrollment": enrollment,
                                "marks": float(mark.marks_obtained),
                                "total": float(mark.total_marks),
                                "percentage": (float(mark.marks_obtained) / float(mark.total_marks)) * 100
                            })
                    
                    if subject_marks:
                        subject_marks.sort(key=lambda x: x["percentage"], reverse=True)
                        top = subject_marks[0]
                        subject_toppers.append({
                            "subject": subject.name,
                            "student_name": top["enrollment"].student.student_name,
                            "marks": f"{top['marks']}/{top['total']}",
                            "percentage": round(top["percentage"], 1)
                        })
                
                if top_students:
                    top_performers_by_class.append({
                        "class": std.name,
                        "total_students": len(enrollments),
                        "overall_toppers": top_students,
                        "subject_toppers": subject_toppers,
                        "class_average": round(
                            sum(s["percentage"] for s in student_performance) / len(student_performance), 1
                        ) if student_performance else 0
                    })
            
            # ==========================================
            # 9. RECENTLY COMPLETED EXAMS
            # ==========================================
            recently_completed = ExamSchedule.objects.filter(
                academic_year=active_year,
                end_date__lt=today,
                end_date__gte=today - timezone.timedelta(days=14)
            ).order_by('-end_date')[:5]

            completed_exams_list = []
            for e in recently_completed:
                classes_list = list(e.classes.all()[:3])
                classes_str = ", ".join([c.name for c in classes_list])
                if e.classes.count() > 3:
                    classes_str += f" and {e.classes.count()-3} more"
                
                completed_exams_list.append({
                    "id": e.id,
                    "name": e.exam_type.name if e.exam_type else None,
                    "term": e.exam_type.term.name if e.exam_type and e.exam_type.term else None,
                    "end_date": e.end_date,
                    "classes": classes_str
                })

            # ==========================================
            # 10. QUICK STATS SUMMARY
            # ==========================================
            quick_stats = {
                "total_scheduled_exams": total_scheduled_exams,
                "upcoming_exams": upcoming_exams,
                "ongoing_exams": ongoing_exams,
                "completed_exams": completed_exams,
                "marks_completion_percentage": marks_completion_percentage,
                "pending_approvals": pending_approvals_count,
                "subjects_without_marks": len(subjects_without_marks),
                "total_students_enrolled": Enrollment.objects.filter(
                    academic_year=active_year,
                    is_active=True
                ).count()
            }

            # ==========================================
            # 11. ALERTS AND WARNINGS
            # ==========================================
            alerts = []
            
            # Alert: Exams ending today
            exams_ending_today = ExamSchedule.objects.filter(
                academic_year=active_year,
                end_date=today
            ).count()
            if exams_ending_today > 0:
                alerts.append({
                    "type": "info",
                    "message": f"{exams_ending_today} exam(s) ending today. Please ensure marks are uploaded."
                })
            
            # Alert: Exams with no marks uploaded
            if len(subjects_without_marks) > 0:
                alerts.append({
                    "type": "warning",
                    "message": f"{len(subjects_without_marks)} subject-exam combinations have no marks uploaded."
                })
            
            # Alert: Low completion percentage (< 50%)
            if marks_completion_percentage < 50 and total_possible_mark_entries > 0:
                alerts.append({
                    "type": "warning",
                    "message": f"Marks upload completion is only {marks_completion_percentage}%. This is below target (50%)."
                })
            
            # Alert: Pending approvals
            if pending_approvals_count > 0:
                alerts.append({
                    "type": "info",
                    "message": f"You have {pending_approvals_count} pending mark change request(s) to review."
                })
            
            # Alert: No active academic year
            if not active_year:
                alerts.append({
                    "type": "critical",
                    "message": "No active academic year configured. Please set up the current academic year."
                })

            # ==========================================
            # FINAL RESPONSE
            # ==========================================
            response_payload = {
                "status": 200,
                "message": "Admin exam overview retrieved successfully",
                "academic_year": {
                    "name": active_year.name,
                    "start_date": active_year.start_date,
                    "end_date": active_year.end_date,
                    "is_current": active_year.is_current
                },
                "timestamp": today.isoformat(),
                "data": {
                    "quick_stats": quick_stats,
                    "exam_breakdown": {
                        "by_status": {
                            "upcoming": upcoming_exams,
                            "ongoing": ongoing_exams,
                            "completed": completed_exams
                        },
                        "by_term": term_breakdown
                    },
                    "marks_upload_status": {
                        "total_marks_entered": marks_uploaded_count,
                        "total_marks_expected": total_possible_mark_entries,
                        "completion_percentage": marks_completion_percentage,
                        "pending_subjects": pending_mark_entry
                    },
                    "class_completion_summary": class_completion[:10],
                    "top_performers": top_performers_by_class,
                    "recent_activity": {
                        "new_exams_scheduled": recent_exams,
                        "recent_uploads": formatted_recent_uploads,
                        "recently_completed_exams": completed_exams_list
                    },
                    "pending_tasks": {
                        "approval_requests": pending_approvals_count,
                        "marks_to_upload": len(subjects_without_marks)
                    },
                    "alerts": alerts
                }
            }
            return Response(self._to_json_safe(response_payload), status=status.HTTP_200_OK)

        except AcademicYear.DoesNotExist:
            return Response(
                {"error": "Academic Year not found. Please check your configuration."},
                status=status.HTTP_404_NOT_FOUND
            )
        except Exception as e:
            return Response(
                {"error": f"An unexpected error occurred while fetching exam overview: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    def _to_json_safe(self, payload):
        """
        Convert nested date/datetime/Decimal values to JSON-native values
        before encryption middleware serializes response.data.
        """
        return json.loads(json.dumps(payload, cls=DjangoJSONEncoder))

class AdminCalendarWidgetView(APIView):
    permission_classes = [IsAuthenticated, IsOwnerAdminOrAdminStaff]

    def get(self, request):
        try:
            month = int(request.query_params.get('month', datetime.now().month))
            year = int(request.query_params.get('year', datetime.now().year))
            
            if not (1 <= month <= 12):
                return Response({"error": "Month must be between 1 and 12"}, status=400)
                
        except ValueError:
            return Response({"error": "Invalid month/year format"}, status=400)
        
        # Get active academic year
        try:
            active_year = AcademicYear.objects.get(is_current=True)
        except AcademicYear.DoesNotExist:
            return Response({"error": "No Active Academic Year Configured"}, status=500)
        
        # Calculate date range
        _, last_day = monthrange(year, month)
        start_date = datetime(year, month, 1).date()
        end_date = datetime(year, month, last_day).date()
        today = datetime.now().date()
        
        events_by_date = defaultdict(list)
        summary = {"exams": 0, "holidays": 0, "announcements": 0}
        
        # 1. EXAMS
        exams = ExamSchedule.objects.filter(
            academic_year=active_year
        ).filter(
            Q(start_date__lte=end_date, end_date__gte=start_date) |
            Q(details__exam_date__range=[start_date, end_date])
        ).distinct().prefetch_related('details', 'classes')
        
        for exam in exams:
            # Exam start/end dates
            if start_date <= exam.start_date <= end_date:
                date_key = exam.start_date.isoformat()
                description = f"{exam.exam_type.name} Starts"
                detailed = f"{exam.exam_type.name} ({exam.exam_type.term.name})"
                
                events_by_date[date_key].append({
                    "type": "exam",
                    "description": description,
                    "detailed_description": detailed,
                })
                summary["exams"] += 1
            
            if start_date <= exam.end_date <= end_date:
                date_key = exam.end_date.isoformat()
                description = f"{exam.exam_type.name} Ends"
                detailed = f"{exam.exam_type.name} ({exam.exam_type.term.name})"
                
                events_by_date[date_key].append({
                    "type": "exam",
                    "description": description,
                    "detailed_description": detailed,
                })
                summary["exams"] += 1
            
            # Subject exams
            for detail in exam.details.all():
                if start_date <= detail.exam_date <= end_date:
                    date_key = detail.exam_date.isoformat()
                    
                    if detail.exam_date == today:
                        desc_prefix = "Today: "
                    elif exam.start_date <= today <= exam.end_date:
                        desc_prefix = "Ongoing: "
                    else:
                        desc_prefix = ""
                    
                    description = f"{desc_prefix}{detail.subject_name} Exam"
                    
                    classes_list = list(exam.classes.all())
                    classes_str = ", ".join([str(cls) for cls in classes_list[:3]])
                    if len(classes_list) > 3:
                        classes_str += f" and {len(classes_list)-3} more"
                    
                    detailed = f"{detail.subject_name} - {classes_str} - {detail.session}"
                    
                    events_by_date[date_key].append({
                        "type": "exam",
                        "description": description,
                        "detailed_description": detailed,
                    })
                    summary["exams"] += 1
        
        # 2. HOLIDAYS
        from holidays.models import Holiday
        
        holidays = Holiday.objects.filter(
            date__range=[start_date, end_date]
        ).order_by('date')
        
        for holiday in holidays:
            date_key = holiday.date.isoformat()
            description = holiday.name
            detailed = f"Holiday - {holiday.get_applicable_for_display()}"
            
            events_by_date[date_key].append({
                "type": "holiday",
                "description": description,
                "detailed_description": detailed,
            })
            summary["holidays"] += 1
        
        # 3. COMMON ANNOUNCEMENTS
        from announcements.models import CommonAnnouncement
        
        announcements = CommonAnnouncement.objects.filter(
            academic_year=active_year,
            date__range=[start_date, end_date]
        ).order_by('date')
        
        for announcement in announcements:
            date_key = announcement.date.isoformat()
            
            events_by_date[date_key].append({
                "type": "common_announcement",
                "description": announcement.title,
                "detailed_description": announcement.description[:100] + "..." if len(announcement.description) > 100 else announcement.description,
            })
            summary["announcements"] += 1
        
        # Sort events within each date
        for date_key in events_by_date:
            events_by_date[date_key].sort(key=lambda x: x["type"])
        
        # Month name
        month_names = [
            "January", "February", "March", "April", "May", "June",
            "July", "August", "September", "October", "November", "December"
        ]
        month_name = month_names[month - 1]
        
        return Response({
            "status": 200,
            "month": month,
            "year": year,
            "month_name": month_name,
            "active_academic_year": active_year.name,
            "total_events": sum(summary.values()),
            "events_by_date": dict(events_by_date),
            "summary": summary
        })
