from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.shortcuts import get_object_or_404
from django.db.models import Avg, Sum, Q

# Models
from .models import BehaviorReport
from students.models import Student, Section, Enrollment
from subjects.models import Subject
from teachers.models import TeacherAllocation, Teacher
from exams.models import StudentMark, ExamType, ExamTerm
from school.models import AcademicYear
from academics.models import ClassTeacher
from timetable.models import TimetableSlot  # <--- CRITICAL NEW IMPORT
from notifications.utils import send_notification_to_users

# Permissions
from schooladmin.permissions import IsAdmin

# ==========================================
# HELPER FUNCTIONS
# ==========================================
def get_behavior_type(score):
    if score >= 4: return "Excellent"
    if score >= 3: return "Good"
    if score >= 2: return "Average"
    if score >= 1: return "Poor"
    return "Needs Improvement"

def calculate_grade(percentage):
    if percentage >= 91: return 'S'
    if percentage >= 81: return 'A'
    if percentage >= 71: return 'B'
    if percentage >= 61: return 'C'
    if percentage >= 51: return 'D'
    if percentage >= 40: return 'E'
    return 'F'

# --- YEAR-SAFE HELPER ---
def get_academic_year(request):
    """
    Determines context:
    - Admin: Can request ?year=2023-2024. Defaults to Active.
    - Teacher/Student: ALWAYS Active Year.
    """
    try:
        active_year = AcademicYear.objects.get(is_current=True)
    except AcademicYear.DoesNotExist:
        # Fallback for Admins setting up system, else 500
        if IsAdmin().has_permission(request, None): return None
        raise 

    # Admin Time Travel Logic
    if IsAdmin().has_permission(request, None):
        requested_year = request.query_params.get('year_id') or request.query_params.get('year')
        if requested_year:
            try:
                if requested_year.isdigit():
                    return AcademicYear.objects.get(id=requested_year)
                else:
                    return AcademicYear.objects.get(name=requested_year)
            except AcademicYear.DoesNotExist:
                pass 
    
    return active_year

# ==========================================
# 1. POST BEHAVIOR REPORT (TIMETABLE-SECURED)
# ==========================================
class PostBehaviorView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        user = request.user
        if getattr(user, 'user_type', '') != 'teacher': 
            return Response({"error": "Unauthorized"}, 403)

        # 1. STRICT YEAR LOCK (Teachers cannot post to past)
        try:
            active_year = AcademicYear.objects.get(is_current=True)
        except AcademicYear.DoesNotExist:
            return Response({"error": "No Active Academic Year Configured"}, 500)

        # 2. Capture Inputs
        term_name = request.data.get('term')
        cls = request.data.get('class')
        sec = request.data.get('section')
        sub_name = request.data.get('subject')
        report_list = request.data.get('reports')
        
        if not all([term_name, cls, sec, sub_name]):
             return Response({"error": "Missing required fields (term, class, section, subject)"}, 400)

        # 3. Get Term (Strict Lookup by Active Year)
        try:
            term_obj = ExamTerm.objects.get(name__iexact=term_name, academic_year=active_year)
        except ExamTerm.DoesNotExist:
            return Response({"error": f"Term '{term_name}' not found for the active year"}, 404)

        try:
            # 4. Get Subject & Teacher
            subject = Subject.objects.get(name__iexact=sub_name, standard__name=cls)
            teacher_profile = Teacher.objects.get(user=user)
            
            # 5. Get Target Section
            target_section = Section.objects.get(standard__name=cls, name=sec)

            # 6. STRICT PERMISSION CHECK (TIMETABLE)
            # Replaced 'TeacherAllocation' with 'TimetableSlot'
            # Logic: "Does this teacher have a slot for THIS Subject in THIS Section?"
            has_permission = TimetableSlot.objects.filter(
                academic_year=active_year,
                teacher=teacher_profile,
                section=target_section,
                subject=subject
            ).exists()
            
            if not has_permission:
                # Optional: Allow Class Teachers to post behavior for any subject?
                # Usually behavior reports are strictly subject-based.
                return Response({
                    "error": f"Permission Denied: You are not scheduled to teach {sub_name} to Class {cls}-{sec} in the active year."
                }, 403)

        except Subject.DoesNotExist:
            return Response({"error": f"Subject '{sub_name}' not found for Class {cls}"}, 404)
        except Section.DoesNotExist:
            return Response({"error": f"Section '{sec}' not found for Class {cls}"}, 404)
        except Teacher.DoesNotExist:
            return Response({"error": "Teacher profile not found"}, 403)

        # 7. Process Reports (Using ENROLLMENT)
        processed_count = 0
        errors = []
        notified_users = []

        for item in report_list:
            s_id = item.get('student_id')
            try:
                # YEAR-SAFE: Find Enrollment for Active Year
                enrollment = Enrollment.objects.get(
                    student__student_id=s_id,
                    academic_year=active_year,
                    is_active=True
                )
                
                # Double check enrollment section matches target section
                if enrollment.section != target_section:
                    errors.append(f"Student {s_id} does not belong to {cls}-{sec} in active year")
                    continue

                # Create/Update Report linked to ENROLLMENT
                report, created = BehaviorReport.objects.update_or_create(
                    enrollment=enrollment,  # <--- NEW KEY
                    subject=subject,
                    term=term_obj,
                    defaults={
                        'teacher': user,
                        'participation': item.get('class_participation', 3),
                        'responsibility': item.get('homework_responsibility', 3),
                        'discipline': item.get('classroom_discipline', 3),
                        'attitude': item.get('learning_attitude', 3),
                        'collaboration': item.get('social_behaviour', 3),
                        'remarks': item.get('remarks', "")
                    }
                )
                processed_count += 1
                if enrollment.student.user:
                    notified_users.append(enrollment.student.user)
            except Enrollment.DoesNotExist:
                errors.append(f"Student {s_id}: Not enrolled in active year")
            except Exception as e:
                errors.append(f"Student {s_id}: {str(e)}")

        if processed_count and notified_users:
            try:
                send_notification_to_users(
                    users=list({student_user.id: student_user for student_user in notified_users}.values()),
                    sender=user,
                    title=f"Behaviour Report: {subject.name}",
                    message=f"{teacher_profile.name} posted your {term_obj.name} behaviour report.",
                    notif_type="Behaviour",
                )
            except Exception as e:
                print(f"Notification Error: {e}")

        return Response({
            "message": f"Processed {processed_count} reports for {term_name} in {cls}-{sec}.", 
            "errors": errors
        }, 200)

    def get(self, request):
        """
        Fetch a specific behavior report.
        STRICTLY CHECKS TIMETABLE PERMISSIONS like POST.
        """
        user = request.user
        if getattr(user, 'user_type', '') != 'teacher': 
            return Response({"error": "Unauthorized"}, 403)

        # 1. Active Year Lock
        try:
            active_year = AcademicYear.objects.get(is_current=True)
        except AcademicYear.DoesNotExist:
            return Response({"error": "No Active Academic Year"}, 500)

        # 2. Get Params
        s_id = request.query_params.get('student_id')
        term_name = request.query_params.get('term')
        subject_name = request.query_params.get('subject')

        if not all([s_id, term_name, subject_name]):
            return Response({"error": "Params 'student_id', 'term', and 'subject' are required"}, 400)

        try:
            # 3. Get Basics
            teacher_profile = Teacher.objects.get(user=user)
            
            # Fetch Enrollment first (To get the Student's Section)
            enrollment = Enrollment.objects.get(
                student__student_id=s_id,
                academic_year=active_year,
                is_active=True
            )
            
            # Fetch Subject (To link with Permission)
            # Note: We use enrollment.section.standard to ensure subject belongs to correct class
            subject = Subject.objects.get(
                name__iexact=subject_name, 
                standard=enrollment.section.standard
            )

            # 4. STRICT PERMISSION CHECK (TIMETABLE)
            # "Does this teacher teach THIS subject to THIS student's section?"
            has_permission = TimetableSlot.objects.filter(
                academic_year=active_year,
                teacher=teacher_profile,
                section=enrollment.section, # Uses the student's actual section
                subject=subject
            ).exists()

            if not has_permission:
                return Response({
                    "error": f"Permission Denied: You are not scheduled to teach {subject.name} to {enrollment.section.standard.name}-{enrollment.section.name}."
                }, 403)

            # 5. Fetch the Report
            # 5. Fetch the Report
            report = BehaviorReport.objects.get(
                enrollment=enrollment,
                term__name__iexact=term_name,
                term__academic_year=active_year, # <--- YEAR-SAFE TERM LOCK
                subject=subject,
                teacher=user 
            )

            data = {
                "student_id": enrollment.student.student_id,
                "student_name": enrollment.student.student_name,
                "class": f"{enrollment.section.standard.name}-{enrollment.section.name}",
                "term": report.term.name,
                "subject": report.subject.name,
                "reports": {
                    "class_participation": report.participation,
                    "homework_responsibility": report.responsibility,
                    "classroom_discipline": report.discipline,
                    "learning_attitude": report.attitude,
                    "social_behaviour": report.collaboration,
                    "remarks": report.remarks
                },
                "average_score": report.average_score,
                "last_updated": report.updated_at
            }
            return Response(data, 200)

        except Enrollment.DoesNotExist:
            return Response({"error": "Student not enrolled in active year"}, 404)
        except Subject.DoesNotExist:
            return Response({"error": "Subject not found for this class"}, 404)
        except Teacher.DoesNotExist:
            return Response({"error": "Teacher profile error"}, 403)
        except BehaviorReport.DoesNotExist:
            return Response({"error": "No report found for this criteria."}, 404)

# ==========================================
# 2. CLASS TEACHER DASHBOARD (YEAR-SAFE)
# ==========================================
class ClassTeacherReportDashboard(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        # Strict Lock for Teachers
        try:
            active_year = AcademicYear.objects.get(is_current=True)
        except AcademicYear.DoesNotExist:
            return Response({"error": "No Active Year"}, 500)

        exam_name = request.query_params.get('exam_type') 
        term_name = request.query_params.get('term')

        if not exam_name:
            return Response({"error": "exam_type parameter is required"}, 400)

        # 1. Verify Class Teacher (UPDATED LOGIC)
        try:
            teacher_profile = Teacher.objects.get(user=user)
            
            # Use ClassTeacher model to find section
            ct_record = ClassTeacher.objects.get(teacher=teacher_profile, academic_year=active_year)
            my_section = ct_record.section
            
        except Teacher.DoesNotExist:
             return Response({"error": "Teacher profile error"}, 403)
        except ClassTeacher.DoesNotExist:
             return Response({"error": "You are not assigned as a Class Teacher for the active year."}, 403)

        # 2. Fetch Exam Object
        try:
            if term_name:
                exam_obj = ExamType.objects.get(name__iexact=exam_name, term__name__iexact=term_name, term__academic_year=active_year)
            else:
                exam_obj = ExamType.objects.filter(name__iexact=exam_name, term__academic_year=active_year).first()
                if not exam_obj: raise ExamType.DoesNotExist
        except ExamType.DoesNotExist:
            return Response({"error": f"Exam '{exam_name}' not found"}, 404)

        # 3. Fetch Data (Via ENROLLMENT)
        # Find Enrollments for this section in the Active Year
        enrollments = Enrollment.objects.filter(
            section=my_section, 
            academic_year=active_year
        ).select_related('student')

        student_data_list = []
        class_stats = { "S": 0, "A": 0, "B": 0, "C": 0, "D": 0, "E": 0, "F": 0, "Total_Pass": 0, "Total_Fail": 0 }

        # Optimization: Fetch all marks for these enrollments at once
        all_marks = StudentMark.objects.filter(enrollment__in=enrollments, schedule__exam_type=exam_obj)

        for enroll in enrollments:
            marks_qs = [m for m in all_marks if m.enrollment_id == enroll.id]
            if not marks_qs: continue

            total_obtained = sum(m.marks_obtained for m in marks_qs)
            total_max = sum(m.total_marks for m in marks_qs)
            
            failed_subjects = [m for m in marks_qs if m.grade_point == 'F']
            is_fail = len(failed_subjects) > 0
            percentage = (total_obtained / total_max * 100) if total_max > 0 else 0
            
            overall_grade = 'F' if is_fail else calculate_grade(percentage)

            if overall_grade in class_stats: class_stats[overall_grade] += 1
            if overall_grade == 'F': class_stats["Total_Fail"] += 1
            else: class_stats["Total_Pass"] += 1

            student_data_list.append({
                "student_id": enroll.student.student_id,
                "name": enroll.student.student_name,
                "raw_total": total_obtained, 
                "summative_total": f"{total_obtained}/{total_max}",
                "percentage": f"{round(percentage, 1)}%",
                "overall_grade": overall_grade,
                "rank": None 
            })

        student_data_list.sort(key=lambda x: x['raw_total'], reverse=True)
        for index, student in enumerate(student_data_list):
            student['rank'] = index + 1
            del student['raw_total'] 

        return Response({
            "exam_type": f"{exam_obj.name} ({exam_obj.term.name})",
            "class_stats": class_stats,
            "students": student_data_list
        }, 200)

# ==========================================
# 3. INDIVIDUAL DETAIL VIEW (YEAR-SAFE + TIME TRAVEL)
# ==========================================
class StudentDetailedReportView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        # 1. Use Helper (Admin can see history)
        active_year = get_academic_year(request)
        if not active_year: return Response({"error": "No Active Year"}, 500)

        s_id = request.query_params.get('student_id')
        target_subject_name = request.query_params.get('subject') 
        exam_name = request.query_params.get('exam_type') 
        term_name = request.query_params.get('term')

        if not all([s_id, target_subject_name, exam_name, term_name]):
             return Response({"error": "Params 'student_id', 'subject', 'exam_type', 'term' required"}, 400)

        try:
            # Fetch Enrollment first (Year Safe)
            enrollment = Enrollment.objects.get(
                student__student_id=s_id,
                academic_year=active_year
            )
            student_obj = enrollment.student # Get actual student profile

            # Find Exam & Term (Year-Safe)
            exam_obj = ExamType.objects.get(name__iexact=exam_name, term__name__iexact=term_name, term__academic_year=active_year)
            term_obj = ExamTerm.objects.get(name__iexact=term_name, academic_year=active_year)

            # Find Subject (Linked to Standard)
            target_subject = Subject.objects.get(
                name__iexact=target_subject_name, 
                standard=enrollment.section.standard
            )
            
        except (Enrollment.DoesNotExist, ExamType.DoesNotExist, Subject.DoesNotExist, ExamTerm.DoesNotExist):
            return Response({"error": "Invalid Student, Exam, Subject, Term or not enrolled in selected year"}, 404)

        # A. FETCH MARKS (Via Enrollment & Schedule)
        marks_data = {}
        exam_records = StudentMark.objects.filter(enrollment=enrollment, schedule__exam_type=exam_obj)
        
        my_total_marks = 0
        for rec in exam_records:
            marks_data[rec.subject.name] = rec.marks_obtained
            my_total_marks += rec.marks_obtained

        # B. CALCULATE RANK (Via Enrollment within Section)
        rank_display = None
        classmates = Enrollment.objects.filter(section=enrollment.section, academic_year=active_year)
        totals_list = []
        
        for mate in classmates:
            total = StudentMark.objects.filter(enrollment=mate, schedule__exam_type=exam_obj).aggregate(
                total=Sum('marks_obtained')
            )['total'] or 0
            totals_list.append(total)
        
        totals_list.sort(reverse=True)
        
        if my_total_marks > 0:
            try:
                my_rank = totals_list.index(my_total_marks) + 1
                if my_rank <= 5:
                    rank_display = f"Rank {my_rank}"
            except ValueError:
                pass 

        # C. FETCH BEHAVIOR (Via Enrollment)
        behavior_data = {}
        try:
            report = BehaviorReport.objects.get(
                enrollment=enrollment, # <--- NEW LINK
                subject=target_subject, 
                term=term_obj 
            )
            
            try:
                teacher_name = report.teacher.teacher_profile.name
            except AttributeError:
                teacher_name = report.teacher.username

            behavior_data = {
                "subject_name": report.subject.name,
                "posted_by": teacher_name,
                "ratings": {
                    "Class Participation": report.participation,
                    "Homework Responsibility": report.responsibility,
                    "Classroom Discipline": report.discipline,
                    "Learning Attitude": report.attitude,
                    "Social Behaviour": report.collaboration
                },
                "average_score": report.average_score,
                "type": get_behavior_type(report.average_score),
                "remarks": report.remarks
            }
        except BehaviorReport.DoesNotExist:
            behavior_data = {
                "subject_name": target_subject_name, 
                "status": "Report not yet submitted for this Term."
            }

        response_data = {
            "student_id": s_id,
            "name": student_obj.student_name,
            "year": active_year.name,
            "term": term_name,
            "exam_type": exam_name,
            "all_subject_marks": marks_data, 
            "subject_behavior_report": behavior_data 
        }

        if rank_display:
            response_data["rank"] = rank_display

        return Response(response_data, 200)

# ==========================================
# 4. VIEW MY REPORTS (Teacher - YEAR SAFE)
# ==========================================
class TeacherViewBehavior(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        try:
            active_year = AcademicYear.objects.get(is_current=True) # Strict for Teacher
        except AcademicYear.DoesNotExist:
            return Response({"error": "No Active Year"}, 500)

        cls = request.query_params.get('class')
        sec = request.query_params.get('section')
        sub = request.query_params.get('subject')
        term_name = request.query_params.get('term')
        specific_student_id = request.query_params.get('student_id')

        if not all([cls, sec, sub, term_name]):
            return Response({"error": "Params 'class', 'section', 'subject', 'term' required"}, 400)

        # FILTER BY ENROLLMENT
        reports = BehaviorReport.objects.filter(
            teacher=user,
            subject__name__iexact=sub,
            term__name__iexact=term_name,
            term__academic_year=active_year, # <--- STRICT TERM LOCK
            enrollment__section__standard__name=cls,
            enrollment__section__name=sec,
            enrollment__academic_year=active_year # <--- YEAR FILTER
        )

        if specific_student_id:
            reports = reports.filter(enrollment__student__student_id=specific_student_id)

        data = []
        for r in reports:
            data.append({
                "student_id": r.enrollment.student.student_id,
                "name": r.enrollment.student.student_name,
                "average": r.average_score,
                "type": get_behavior_type(r.average_score)
            })

        return Response({
            "class": cls,
            "section": sec,
            "subject": sub,
            "term": term_name,
            "total_reports": len(data),
            "reports": data
        }, 200)

# ==========================================
# 5. CLASS BEHAVIOR ANALYSIS (YEAR-SAFE)
# ==========================================
class ClassBehaviorAnalysisView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        try:
            active_year = AcademicYear.objects.get(is_current=True)
        except AcademicYear.DoesNotExist:
             return Response({"error": "No Active Year"}, 500)

        term_name = request.query_params.get('term')

        if not term_name:
            return Response({"error": "'term' parameter is required"}, 400)

        # 1. Verify Class Teacher (UPDATED)
        try:
            teacher_profile = Teacher.objects.get(user=user)
            # Find Section via ClassTeacher model
            ct_record = ClassTeacher.objects.get(teacher=teacher_profile, academic_year=active_year)
            my_section = ct_record.section
        except Teacher.DoesNotExist:
             return Response({"error": "Teacher error"}, 403)
        except ClassTeacher.DoesNotExist:
             return Response({"error": "You are not a Class Teacher for the active year."}, 403)

        # Iterate Enrollments
        enrollments = Enrollment.objects.filter(section=my_section, academic_year=active_year)
        student_list = []
        behavior_stats = { "Excellent": 0, "Good": 0, "Average": 0, "Poor": 0, "Needs Improvement": 0 }

        for enroll in enrollments:
            # Filter by TERM & ENROLLMENT (Strict Term Lock)
            reports = BehaviorReport.objects.filter(
                enrollment=enroll, 
                term__name__iexact=term_name,
                term__academic_year=active_year
            )
            
            if reports.exists():
                avg_score = sum(r.average_score for r in reports) / reports.count()
                final_avg = round(avg_score, 1)
                b_type = get_behavior_type(final_avg)
                if b_type in behavior_stats: behavior_stats[b_type] += 1
            else:
                final_avg = 0
                b_type = "Not Rated"

            student_list.append({
                "student_id": enroll.student.student_id,
                "name": enroll.student.student_name,
                "overall_score": f"{final_avg}/5",
                "type": b_type
            })

        return Response({
            "term": term_name,
            "behavior_distribution": behavior_stats,
            "students": student_list
        }, 200)

# ==========================================
# 6. STUDENT BEHAVIOR DASHBOARD (YEAR-SAFE)
# ==========================================
class StudentBehaviorDashboardView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        try:
            active_year = AcademicYear.objects.get(is_current=True)
        except AcademicYear.DoesNotExist:
             return Response({"error": "No Active Year"}, 500)

        try:
            student = request.user.student_profile
            # Validate Enrollment
            enrollment = Enrollment.objects.get(student=student, academic_year=active_year)
        except (AttributeError, Enrollment.DoesNotExist):
            return Response({"error": "Access Denied or Not Enrolled"}, 403)

        req_trait = request.query_params.get('trait')     
        req_subject = request.query_params.get('subject') 
        req_term = request.query_params.get('term')  

        # FILTER REPORTS BY ENROLLMENT
        reports = BehaviorReport.objects.filter(enrollment=enrollment)
        
        all_terms = ExamTerm.objects.filter(academic_year=active_year).order_by('rank')

        # --- HELPER: Build Dynamic Graph Data ---
        def build_dynamic_graph(view_name, get_value_func, extra_data=None):
            graph_data = []
            previous_val = None

            for term in all_terms:
                current_val = get_value_func(term)
                
                change = 0
                trend = "neutral"
                if previous_val is not None and current_val > 0 and previous_val > 0:
                    diff = current_val - previous_val
                    change = round((diff / previous_val) * 100, 2)
                    if diff > 0: trend = "increase"
                    elif diff < 0: trend = "decrease"
                
                graph_data.append({
                    "exam": term.name, 
                    "value": current_val,
                    "change_percentage": change,
                    "trend": trend
                })

                if current_val > 0: previous_val = current_val
            
            response = {
                "status": 200,
                "type": "behaviour",
                "student": student.student_name,
                "view": view_name,
                "graph_data": graph_data
            }
            if extra_data: response.update(extra_data)
            return Response(response)

        # MODE 1: TERM BREAKDOWN
        if req_term:
            term_reports = reports.filter(term__name__iexact=req_term)
            breakdown_data = []
            for r in term_reports:
                breakdown_data.append({
                    "subject": r.subject.name,
                    "aggregated_score": r.average_score,
                    "max_score": 5.0
                })
            return Response({
                "status": 200,
                "student": student.student_name,
                "term": req_term,
                "breakdown": breakdown_data
            })

        # MODE 2: COMBINED (Subject + Trait)
        if req_trait and req_subject:
            trait_field = req_trait.lower()
            sub_reports = reports.filter(subject__name__iexact=req_subject)

            def val_func(term_obj):
                rep = sub_reports.filter(term=term_obj).first()
                return float(getattr(rep, trait_field, 0.0)) if rep else 0.0

            return build_dynamic_graph("Subject Trait Trend", val_func, 
                                     {"subject": req_subject, "behaviour_category": req_trait})

        # MODE 3: SUBJECT TREND
        if req_subject:
            sub_reports = reports.filter(subject__name__iexact=req_subject)
            def val_func(term_obj):
                rep = sub_reports.filter(term=term_obj).first()
                return float(rep.average_score) if rep else 0.0

            return build_dynamic_graph("Subject Trend", val_func, {"subject": req_subject})

        # MODE 4: TRAIT TREND
        if req_trait:
            trait_field = req_trait.lower()
            def val_func(term_obj):
                subset = reports.filter(term=term_obj)
                val = subset.aggregate(Avg(trait_field))[f'{trait_field}__avg']
                return round(val, 1) if val else 0.0
            
            return build_dynamic_graph("Trait Trend", val_func, {"behaviour_category": req_trait})

        # MODE 5: OVERALL AGGREGATE
        def val_func(term_obj):
            subset = reports.filter(term=term_obj)
            if not subset.exists(): return 0.0
            scores = [r.average_score for r in subset]
            return round(sum(scores) / len(scores), 1)

        return build_dynamic_graph("Overall Behavior", val_func)


# ==========================================
# 4. EDIT BEHAVIOR REPORT (YEAR-SAFE)
# ==========================================
class EditBehaviorView(APIView):
    permission_classes = [IsAuthenticated]

    def put(self, request):
        user = request.user
        try:
            active_year = AcademicYear.objects.get(is_current=True) # Strict Lock
        except AcademicYear.DoesNotExist:
             return Response({"error": "No Active Year"}, 500)

        student_id = request.data.get('student_id')
        subject_name = request.data.get('subject')
        term_name = request.data.get('term')
        
        if not all([student_id, subject_name, term_name]):
            return Response({"error": "student_id, subject, and term are required"}, 400)

        try:
            # YEAR-SAFE: Find Enrollment
            enrollment = Enrollment.objects.get(
                student__student_id=student_id, 
                academic_year=active_year
            )
            
            # Find Report via Enrollment
            # Find Report via Enrollment
            report = BehaviorReport.objects.get(
                enrollment=enrollment, # <--- KEY CHANGE
                subject__name__iexact=subject_name,
                term__name__iexact=term_name,
                term__academic_year=active_year
            )
        except Enrollment.DoesNotExist:
            return Response({"error": f"Student {student_id} not enrolled in active year"}, 404)
        except BehaviorReport.DoesNotExist:
            return Response({"error": f"No report found for {subject_name} in {term_name}"}, 404)

        # 3. Permission Check
        if report.teacher != user:
            return Response({"error": "You cannot edit a report posted by another teacher."}, 403)

        # 4. Update Fields
        data = request.data
        if 'class_participation' in data: report.participation = data['class_participation']
        if 'homework_responsibility' in data: report.responsibility = data['homework_responsibility']
        if 'classroom_discipline' in data: report.discipline = data['classroom_discipline']
        if 'learning_attitude' in data: report.attitude = data['learning_attitude']
        if 'social_behaviour' in data: report.collaboration = data['social_behaviour']
        if 'remarks' in data: report.remarks = data['remarks']
        
        report.save()
        return Response({"message": f"Behavior report for {term_name} updated successfully"}, 200)


###### SIVA BRO REPORTS VIEWS # # # # #

class GetSubmittedBehaviorReportsView(APIView):
    """
    API to fetch already submitted behavior reports for editing.
    Returns detailed behavior reports for all students in a class-section
    for a specific term and subject.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        
        # Check if user is a teacher
        if getattr(user, 'user_type', '') != 'teacher':
            return Response({"error": "Unauthorized - Teachers only"}, 403)
        
        # Get query parameters
        cls = request.query_params.get('class')
        sec = request.query_params.get('section')
        term_name = request.query_params.get('term')
        subject_name = request.query_params.get('subject')
        
        # Validate required parameters
        if not all([cls, sec, term_name, subject_name]):
            return Response({
                "error": "Missing required parameters: class, section, term, subject"
            }, 400)
        
        try:
            # 1. Get current academic year FIRST to lock context
            try:
                active_year = AcademicYear.objects.get(is_current=True)
            except AcademicYear.DoesNotExist:
                return Response({"error": "No Active Academic Year Configured"}, 500)

            # Get teacher profile
            teacher_profile = Teacher.objects.get(user=user)
            
            # Get subject
            subject = Subject.objects.get(
                name__iexact=subject_name,
                standard__name=cls
            )
            
            # Get section
            section = Section.objects.get(
                standard__name=cls,
                name=sec
            )
            
            # 2. Get term (YEAR-SAFE LOCK)
            term_obj = ExamTerm.objects.get(name__iexact=term_name, academic_year=active_year)
            
            # IMPORTANT FIX: Verify teacher has permission for this subject-section
            # Use TimetableSlot instead of TeacherAllocation for consistency with PostBehaviorView
            is_allocated = TimetableSlot.objects.filter(
                academic_year=active_year,
                teacher=teacher_profile,
                section=section,
                subject=subject
            ).exists()
            
            if not is_allocated:
                # Alternative: Also check TeacherAllocation if you want to allow that
                try:
                    # Check if teacher is allocated to teach this subject to this class
                    is_allocated = TeacherAllocation.objects.filter(
                        teacher=teacher_profile,
                        subject=subject,
                        standard__name=cls  # FIX: Changed from 'allocated_classes' to 'standard'
                    ).exists()
                except Exception:
                    is_allocated = False
                
                if not is_allocated:
                    return Response({
                        "error": f"You are not assigned to teach {subject_name} for Class {cls}-{sec}"
                    }, 403)
            
            # Get all enrollments in the section for current year
            enrollments = Enrollment.objects.filter(
                section=section,
                academic_year=active_year
            ).select_related('student')
            
            # Prepare response data
            reports_data = []
            total_students = enrollments.count()
            submitted_count = 0
            
            for enrollment in enrollments:
                # Try to get existing behavior report
                try:
                    behavior_report = BehaviorReport.objects.get(
                        enrollment=enrollment,  # FIX: Use enrollment instead of student.user
                        subject=subject,
                        term=term_obj,
                        teacher=user  # Only show reports submitted by this teacher
                    )
                    
                    # Report exists
                    report_info = {
                        "student_id": enrollment.student.student_id,
                        "student_name": enrollment.student.student_name,
                        "class_participation": behavior_report.participation,
                        "homework_responsibility": behavior_report.responsibility,
                        "classroom_discipline": behavior_report.discipline,
                        "learning_attitude": behavior_report.attitude,
                        "social_behaviour": behavior_report.collaboration,
                        "remarks": behavior_report.remarks or "",
                        "average_score": behavior_report.average_score,
                        "type": get_behavior_type(behavior_report.average_score),
                        "is_submitted": True,
                        "submitted_at": behavior_report.created_at.strftime("%Y-%m-%d %H:%M")
                    }
                    submitted_count += 1
                    
                except BehaviorReport.DoesNotExist:
                    # No report exists yet - return default values
                    report_info = {
                        "student_id": enrollment.student.student_id,
                        "student_name": enrollment.student.student_name,
                        "class_participation": 3,  # Default value
                        "homework_responsibility": 3,
                        "classroom_discipline": 3,
                        "learning_attitude": 3,
                        "social_behaviour": 3,
                        "remarks": "",
                        "average_score": 3.0,
                        "type": get_behavior_type(3.0),
                        "is_submitted": False,
                        "submitted_at": None
                    }
                
                reports_data.append(report_info)
            
            # Calculate summary statistics
            submitted_reports = [r for r in reports_data if r['is_submitted']]
            
            if submitted_reports:
                avg_participation = sum(r['class_participation'] for r in submitted_reports) / len(submitted_reports)
                avg_responsibility = sum(r['homework_responsibility'] for r in submitted_reports) / len(submitted_reports)
                avg_discipline = sum(r['classroom_discipline'] for r in submitted_reports) / len(submitted_reports)
                avg_attitude = sum(r['learning_attitude'] for r in submitted_reports) / len(submitted_reports)
                avg_collaboration = sum(r['social_behaviour'] for r in submitted_reports) / len(submitted_reports)
                avg_overall = sum(r['average_score'] for r in submitted_reports) / len(submitted_reports)
            else:
                avg_participation = avg_responsibility = avg_discipline = avg_attitude = avg_collaboration = avg_overall = 0
            
            return Response({
                "status": "success",
                "message": f"Found {submitted_count} submitted reports out of {total_students} students",
                "class": cls,
                "section": sec,
                "term": term_name,
                "subject": subject_name,
                "academic_year": active_year.name,
                "total_students": total_students,
                "submitted_reports": submitted_count,
                "pending_reports": total_students - submitted_count,
                "summary": {
                    "average_participation": round(avg_participation, 1),
                    "average_responsibility": round(avg_responsibility, 1),
                    "average_discipline": round(avg_discipline, 1),
                    "average_attitude": round(avg_attitude, 1),
                    "average_collaboration": round(avg_collaboration, 1),
                    "average_overall": round(avg_overall, 1)
                },
                "reports": reports_data
            }, 200)
            
        except Subject.DoesNotExist:
            return Response({"error": f"Subject '{subject_name}' not found for Class {cls}"}, 404)
        except Section.DoesNotExist:
            return Response({"error": f"Section '{sec}' not found for Class {cls}"}, 404)
        except ExamTerm.DoesNotExist:
            return Response({"error": f"Term '{term_name}' not found for active academic year"}, 404)
        except Teacher.DoesNotExist:
            return Response({"error": "Teacher profile not found"}, 403)
        except Exception as e:
            import traceback
            traceback.print_exc()  # For debugging
            return Response({"error": str(e)}, 500)
