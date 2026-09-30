from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from teachers.permissions import IsTeacher
from django.db.models import Sum, Q
from academics.models import ClassTeacher, Section # <--- Added Section

# Models
from students.models import Student, Enrollment
from subjects.models import Subject
from exams.models import StudentMark, ExamType, ExamTerm
from reports.models import BehaviorReport
from teachers.models import TeacherAllocation
from school.models import AcademicYear
from timetable.models import TimetableSlot  # <--- CRITICAL NEW IMPORT
from school.tenant import get_active_academic_year

from exams.models import ClassTest, ClassTestMarks
from students.permissions import IsStudent
from .utils import generate_trend_analysis

# =========================================================
#  PART A: CLASS TEACHER VIEWS (AGGREGATE)
# =========================================================

class ClassTeacherMarksView(APIView):
    """
    Shows Trend based on AGGREGATE PERCENTAGE across all subjects.
    YEAR-SAFE: Only for Active Year enrollments.
    """
    permission_classes = [IsAuthenticated, IsTeacher]

    def get(self, request):
        # 1. Year Lock
        try:
            active_year = AcademicYear.objects.get(is_current=True)
        except AcademicYear.DoesNotExist:
            return Response({"error": "No Active Year"}, 500)

        input_id = request.query_params.get('student_id')
        term_name = request.query_params.get('term')        
        compare_str = request.query_params.get('compare')   

        if not input_id: return Response({"error": "student_id required"}, 400)

        # 2. Fetch Enrollment (Year Safe)
        try:
            enrollment = Enrollment.objects.get(
                student__student_id=input_id,
                academic_year=active_year
            )
        except Enrollment.DoesNotExist:
            return Response({"error": "Student not enrolled in Active Year"}, 404)

        # 3. Permission (Class Teacher Check via New Model)
        teacher = request.user.teacher_profile
        try:
            ct_record = ClassTeacher.objects.get(teacher=teacher, academic_year=active_year)
            if ct_record.section != enrollment.section:
                return Response({"error": "Permission denied. Not Class Teacher for this student."}, 403)
        except ClassTeacher.DoesNotExist:
            return Response({"error": "Permission denied. You are not a Class Teacher."}, 403)

        # 4. Select Exams (Year-Safe Term Lock)
        all_exams = ExamType.objects.filter(term__academic_year=active_year).select_related('term').order_by('term__rank', 'rank')
        
        if term_name:
            all_exams = all_exams.filter(term__name__iexact=term_name)
        
        if compare_str:
            requested_items = [x.strip() for x in compare_str.split(',')]
            query = Q()
            for item in requested_items:
                if ":" in item:
                    ex_name, tm_name = item.split(':')
                    query |= Q(name__iexact=ex_name.strip(), term__name__iexact=tm_name.strip())
                else:
                    query |= Q(name__iexact=item)
            all_exams = all_exams.filter(query)

        marks_data = {}
        exam_labels_list = []

        for exam in all_exams:
            label = f"{exam.name} ({exam.term.name})"
            exam_labels_list.append(label)
            
            # Fetch Marks via Enrollment & Schedule Bridge
            marks_qs = StudentMark.objects.filter(enrollment=enrollment, schedule__exam_type=exam)
            
            if marks_qs.exists():
                total_obtained = sum([float(m.marks_obtained) for m in marks_qs])
                total_max = sum([float(m.total_marks) for m in marks_qs])
                
                if total_max > 0:
                    percentage = (total_obtained / total_max) * 100
                    marks_data[label] = round(percentage, 1)
                else:
                    marks_data[label] = 0
            else:
                marks_data[label] = 0

        return Response({
            "status": 200,
            "type": "marks",
            "view": "Aggregate (All Subjects)",
            "student": enrollment.student.student_name,
            "filter_applied": term_name if term_name else "All",
            "graph_data": generate_trend_analysis(marks_data, exam_order=exam_labels_list)
        })

class ClassTeacherBehaviourView(APIView):
    permission_classes = [IsAuthenticated, IsTeacher]

    def get(self, request):
        try:
            active_year = AcademicYear.objects.get(is_current=True)
        except AcademicYear.DoesNotExist:
            return Response({"error": "No Active Year"}, 500)

        input_id = request.query_params.get('student_id')
        if not input_id: return Response({"error": "student_id required"}, 400)

        try:
            enrollment = Enrollment.objects.get(
                student__student_id=input_id,
                academic_year=active_year
            )
        except Enrollment.DoesNotExist:
            return Response({"error": "Student not found in Active Year"}, 404)

        teacher = request.user.teacher_profile
        try:
            ct_record = ClassTeacher.objects.get(teacher=teacher, academic_year=active_year)
            if ct_record.section != enrollment.section:
                return Response({"error": "Permission denied"}, 403)
        except ClassTeacher.DoesNotExist:
            return Response({"error": "Permission denied"}, 403)

        # YEAR-SAFE: Only fetch terms for the active year
        all_terms = ExamTerm.objects.filter(academic_year=active_year).order_by('rank')
        behaviour_data = {}

        for term in all_terms:
            # Filter by Enrollment
            reports = BehaviorReport.objects.filter(
                enrollment=enrollment, 
                term=term 
            )
            if reports.exists():
                total = sum([r.average_score for r in reports])
                behaviour_data[term.name] = round(total / reports.count(), 1)
            else:
                behaviour_data[term.name] = 0

        return Response({
            "status": 200,
            "type": "behaviour",
            "student": enrollment.student.student_name,
            "graph_data": generate_trend_analysis(behaviour_data)
        })

# =========================================================
#  PART B: SUBJECT SPECIFIC VIEWS
# =========================================================

class SubjectTeacherMarksView(APIView):
    permission_classes = [IsAuthenticated, IsTeacher]

    def get(self, request):
        try:
            active_year = AcademicYear.objects.get(is_current=True)
        except AcademicYear.DoesNotExist:
            return Response({"error": "No Active Year"}, 500)

        input_id = request.query_params.get('student_id')
        subject_name = request.query_params.get('subject') 
        term_name = request.query_params.get('term')        
        compare_str = request.query_params.get('compare') 

        if not input_id or not subject_name:
            return Response({"error": "Params required"}, 400)

        try:
            enrollment = Enrollment.objects.get(
                student__student_id=input_id, 
                academic_year=active_year
            )
            subject = Subject.objects.get(name__iexact=subject_name, standard=enrollment.section.standard)
        except (Enrollment.DoesNotExist, Subject.DoesNotExist):
            return Response({"error": "Student or Subject Not Found"}, 404)

        teacher = request.user.teacher_profile
        
        # 1. Class Teacher Check
        is_class_teacher = False
        try:
            ct = ClassTeacher.objects.get(teacher=teacher, academic_year=active_year)
            if ct.section == enrollment.section:
                is_class_teacher = True
        except ClassTeacher.DoesNotExist:
            pass

        # 2. Subject Teacher Check (TIMETABLE)
        is_subject_teacher = False
        if not is_class_teacher:
            is_subject_teacher = TimetableSlot.objects.filter(
                academic_year=active_year,
                teacher=teacher,
                section=enrollment.section, # Specific Section
                subject=subject
            ).exists()

        if not is_class_teacher and not is_subject_teacher:
            return Response({"error": "Permission Denied."}, 403)

        # YEAR-SAFE: Only fetch exams linked to terms in the active year
        all_exams = ExamType.objects.filter(term__academic_year=active_year).select_related('term').order_by('term__rank', 'rank')

        if term_name:
            all_exams = all_exams.filter(term__name__iexact=term_name)
        
        if compare_str:
            requested_items = [x.strip() for x in compare_str.split(',')]
            query = Q()
            for item in requested_items:
                if ":" in item:
                    ex_name, tm_name = item.split(':')
                    query |= Q(name__iexact=ex_name.strip(), term__name__iexact=tm_name.strip())
                else:
                    query |= Q(name__iexact=item)
            all_exams = all_exams.filter(query)

        marks_data = {}
        exam_labels_list = []

        for exam in all_exams:
            label = f"{exam.name} ({exam.term.name})"
            exam_labels_list.append(label)
            
            # Fetch by Enrollment & Schedule Bridge
            rec = StudentMark.objects.filter(
                enrollment=enrollment, subject=subject, schedule__exam_type=exam
            ).first()
            
            if rec and rec.total_marks > 0:
                percentage = (float(rec.marks_obtained) / float(rec.total_marks)) * 100
                marks_data[label] = round(percentage, 1)
            else:
                marks_data[label] = 0

        return Response({
            "status": 200,
            "type": "marks",
            "subject": subject.name,
            "viewed_by": "Class Teacher" if is_class_teacher else "Subject Teacher",
            "filter_applied": term_name if term_name else "All",
            "graph_data": generate_trend_analysis(marks_data, exam_order=exam_labels_list)
        })

class SubjectTeacherBehaviourView(APIView):
    permission_classes = [IsAuthenticated, IsTeacher]

    def get(self, request):
        try:
            active_year = AcademicYear.objects.get(is_current=True)
        except AcademicYear.DoesNotExist:
            return Response({"error": "No Active Year"}, 500)

        input_id = request.query_params.get('student_id')
        subject_name = request.query_params.get('subject') 

        if not input_id or not subject_name:
            return Response({"error": "Params required"}, 400)

        try:
            enrollment = Enrollment.objects.get(
                student__student_id=input_id, 
                academic_year=active_year
            )
            subject = Subject.objects.get(name__iexact=subject_name, standard=enrollment.section.standard)
        except:
            return Response({"error": "Student or Subject Not Found"}, 404)

        teacher = request.user.teacher_profile
        
        # 1. Class Teacher Check
        is_class_teacher = False
        try:
            ct = ClassTeacher.objects.get(teacher=teacher, academic_year=active_year)
            if ct.section == enrollment.section:
                is_class_teacher = True
        except ClassTeacher.DoesNotExist:
            pass

        # 2. Subject Teacher Check (TIMETABLE)
        is_subject_teacher = False
        if not is_class_teacher:
            is_subject_teacher = TimetableSlot.objects.filter(
                academic_year=active_year,
                teacher=teacher,
                section=enrollment.section, # Specific Section
                subject=subject
            ).exists()

        if not is_class_teacher and not is_subject_teacher:
            return Response({"error": "Permission Denied."}, 403)

        # YEAR-SAFE: Only fetch terms for the active year
        all_terms = ExamTerm.objects.filter(academic_year=active_year).order_by('rank')
        behaviour_data = {}

        for term in all_terms:
            # Filter by Enrollment
            rec = BehaviorReport.objects.filter(
                enrollment=enrollment, 
                subject=subject, 
                term=term
            ).first()
            behaviour_data[term.name] = rec.average_score if rec else 0

        return Response({
            "status": 200,
            "type": "behaviour",
            "subject": subject.name,
            "viewed_by": "Class Teacher" if is_class_teacher else "Subject Teacher",
            "graph_data": generate_trend_analysis(behaviour_data)
        })

# =========================================================
#  NEW: CLASS TEACHER - SPECIFIC EXAM BREAKDOWN
# =========================================================

class ClassTeacherExamMarksDetailView(APIView):
    permission_classes = [IsAuthenticated, IsTeacher]

    def get(self, request):
        try:
            active_year = AcademicYear.objects.get(is_current=True)
        except AcademicYear.DoesNotExist:
            return Response({"error": "No Active Year"}, 500)

        input_id = request.query_params.get('student_id')
        exam_name = request.query_params.get('exam_type')
        term_name = request.query_params.get('term')

        if not input_id or not exam_name:
            return Response({"error": "student_id and exam_type required"}, 400)

        # 1. Find Specific Exam
        # 1. Find Specific Exam (Year-Safe Lock)
        try:
            exam_qs = ExamType.objects.filter(name__iexact=exam_name, term__academic_year=active_year)
            if term_name:
                exam_qs = exam_qs.filter(term__name__iexact=term_name)
            
            if not exam_qs.exists():
                return Response({"error": "Exam not found"}, 404)
            
            exam_obj = exam_qs.first()
        except Exception as e:
            return Response({"error": str(e)}, 500)

        # 2. Permission Check (Enrollment-Based)
        try:
            enrollment = Enrollment.objects.get(
                student__student_id=input_id,
                academic_year=active_year
            )
        except Enrollment.DoesNotExist:
            return Response({"error": "Student not enrolled"}, 404)

        teacher = request.user.teacher_profile
        
        try:
            ct_record = ClassTeacher.objects.get(teacher=teacher, academic_year=active_year)
            if ct_record.section != enrollment.section:
                return Response({"error": "Permission Denied. Not Class Teacher."}, 403)
        except ClassTeacher.DoesNotExist:
            return Response({"error": "Permission Denied."}, 403)

        # 3. Fetch Marks (Via Enrollment & Schedule Bridge)
        marks = StudentMark.objects.filter(enrollment=enrollment, schedule__exam_type=exam_obj).select_related('subject')

        data = []
        for m in marks:
            data.append({
                "subject": m.subject.name,
                "mark": float(m.marks_obtained),
                "max_mark": float(m.total_marks),
                "grade": m.grade_point
            })

        return Response({
            "status": 200,
            "student": enrollment.student.student_name,
            "exam_type": exam_obj.name,
            "term": exam_obj.term.name,
            "breakdown": data
        })

class ClassTeacherExamBehaviourDetailView(APIView):
    permission_classes = [IsAuthenticated, IsTeacher]

    def get(self, request):
        try:
            active_year = AcademicYear.objects.get(is_current=True)
        except AcademicYear.DoesNotExist:
            return Response({"error": "No Active Year"}, 500)

        input_id = request.query_params.get('student_id')
        term_name = request.query_params.get('term') 

        if not input_id or not term_name:
            return Response({"error": "student_id and term required"}, 400)

        try:
            enrollment = Enrollment.objects.get(
                student__student_id=input_id,
                academic_year=active_year
            )
        except Enrollment.DoesNotExist:
            return Response({"error": "Student not enrolled"}, 404)

        teacher = request.user.teacher_profile
        try:
            ct_record = ClassTeacher.objects.get(teacher=teacher, academic_year=active_year)
            if ct_record.section != enrollment.section:
                return Response({"error": "Permission Denied."}, 403)
        except ClassTeacher.DoesNotExist:
            return Response({"error": "Permission Denied."}, 403)

        # Filter Reports by Enrollment (Strict Term Lock)
        reports = BehaviorReport.objects.filter(
            enrollment=enrollment,
            term__name__iexact=term_name,
            term__academic_year=active_year
        ).select_related('subject')

        data = []
        for r in reports:
            data.append({
                "subject": r.subject.name,
                "aggregated_score": r.average_score,
                "max_score": 5.0
            })

        return Response({
            "status": 200,
            "student": enrollment.student.student_name,
            "term": term_name,
            "breakdown": data
        })


class SubjectTeacherSpecificBehaviourTrendView(APIView):
    permission_classes = [IsAuthenticated, IsTeacher]

    def get(self, request):
        try:
            active_year = AcademicYear.objects.get(is_current=True)
        except AcademicYear.DoesNotExist:
            return Response({"error": "No Active Year"}, 500)

        input_id = request.query_params.get('student_id')
        subject_name = request.query_params.get('subject')
        behaviour_type = request.query_params.get('behaviour_type')

        if not all([input_id, subject_name, behaviour_type]):
            return Response({"error": "Missing params"}, 400)

        try:
            enrollment = Enrollment.objects.get(
                student__student_id=input_id, 
                academic_year=active_year
            )
            target_subject = Subject.objects.get(name__iexact=subject_name, standard=enrollment.section.standard)
        except (Enrollment.DoesNotExist, Subject.DoesNotExist):
            return Response({"error": "Student or Subject not found"}, 404)

        teacher = request.user.teacher_profile
        
        # 1. Class Teacher Check
        is_class_teacher = False
        try:
            ct = ClassTeacher.objects.get(teacher=teacher, academic_year=active_year)
            if ct.section == enrollment.section:
                is_class_teacher = True
        except ClassTeacher.DoesNotExist:
            pass

        # 2. Subject Teacher Check (TIMETABLE)
        is_subject_teacher = False
        if not is_class_teacher:
            is_subject_teacher = TimetableSlot.objects.filter(
                academic_year=active_year,
                teacher=teacher,
                section=enrollment.section, # Specific Section
                subject=target_subject
            ).exists()

        if not is_class_teacher and not is_subject_teacher:
             return Response({
                 "error": f"Permission Denied: You are not allowed to view {subject_name} data."
             }, 403)

        FIELD_MAPPER = {
            'participation': 'participation',
            'responsibility': 'responsibility',
            'discipline': 'discipline',
            'attitude': 'attitude',
            'collaboration': 'collaboration',
            'class_participation': 'participation',
            'homework_responsibility': 'responsibility',
            'classroom_discipline': 'discipline',
            'learning_attitude': 'attitude',
            'social_behaviour': 'collaboration'
        }
        
        db_field = FIELD_MAPPER.get(behaviour_type)
        if not db_field:
             return Response({"error": "Invalid behaviour type"}, 400)

        # YEAR-SAFE: Only fetch terms for the active year
        all_terms = ExamTerm.objects.filter(academic_year=active_year).order_by('rank')
        trend_data = {}

        for term in all_terms:
            # Filter by Enrollment
            report = BehaviorReport.objects.filter(
                enrollment=enrollment, 
                subject=target_subject, 
                term=term
            ).first()
            val = getattr(report, db_field, 0) if report else 0
            trend_data[term.name] = val

        return Response({
            "status": 200,
            "student": enrollment.student.student_name,
            "subject": target_subject.name,
            "viewed_by": "Class Teacher" if is_class_teacher else "Subject Teacher",
            "graph_data": generate_trend_analysis(trend_data)
        })

##upto here

class ClassTestAnalyticsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        
        # 1. Inputs
        student_id_param = request.query_params.get('student_id') # Optional for Student
        term_name = request.query_params.get('term') # "Term 1"
        subject_name = request.query_params.get('subject') # "Maths"

        if not term_name or not subject_name:
            return Response({"error": "Params 'term' and 'subject' are required"}, 400)

        try:
            active_year = AcademicYear.objects.get(is_current=True)
        except: return Response({"error": "No Active Year"}, 500)

        # 2. Resolve Target Student & Check Permissions
        target_student = None

        # --- SCENARIO A: STUDENT (Self View) ---
        if hasattr(user, 'student_profile'):
            # Ignore param, force use of own profile
            target_student = user.student_profile

        # --- SCENARIO B: TEACHER (View Student) ---
        elif hasattr(user, 'teacher_profile'):
            if not student_id_param:
                return Response({"error": "Teacher must provide 'student_id'"}, 400)
            
            try:
                target_student = Student.objects.get(student_id=student_id_param)
            except Student.DoesNotExist:
                return Response({"error": "Student not found"}, 404)

            teacher = user.teacher_profile
            
            # Find Student's Enrollment for this year (to know their Section)
            try:
                enrollment = Enrollment.objects.get(student=target_student, academic_year=active_year)
                student_section = enrollment.section
            except Enrollment.DoesNotExist:
                return Response({"error": "Student not enrolled in Active Year"}, 404)

            # PERMISSION CHECK
            
            # 1. Is he the CLASS TEACHER?
            is_class_teacher = False
            try:
                ct = ClassTeacher.objects.get(teacher=teacher, academic_year=active_year)
                if ct.section == student_section:
                    is_class_teacher = True
            except ClassTeacher.DoesNotExist:
                pass

            # 2. Is he the SUBJECT TEACHER? (TIMETABLE)
            if not is_class_teacher:
                try:
                    subject = Subject.objects.get(name__iexact=subject_name, standard=student_section.standard)
                    is_subject_teacher = TimetableSlot.objects.filter(
                        academic_year=active_year,
                        teacher=teacher,
                        section=student_section,
                        subject=subject
                    ).exists()
                except Subject.DoesNotExist:
                    is_subject_teacher = False

                if not is_subject_teacher:
                    return Response({"error": f"You are not the Class Teacher of {student_section} nor the Subject Teacher for {subject_name}."}, 403)

        else:
            return Response({"error": "Access Denied"}, 403)

        # 3. Fetch Data (Logic remains same)
        # We need to re-fetch enrollment here in case we are in Student mode
        try:
            enrollment = Enrollment.objects.get(student=target_student, academic_year=active_year)
        except Enrollment.DoesNotExist:
            return Response({"error": "Enrollment record not found"}, 404)

        tests = ClassTest.objects.filter(
            academic_year=active_year,
            term__name__iexact=term_name,
            term__academic_year=active_year, # <--- Added for strict Term consistency
            subject__name__iexact=subject_name,
            section=enrollment.section
        ).order_by('created_at')
        
        graph_data = []
        previous_percentage = None

        for test in tests:
            mark_entry = ClassTestMarks.objects.filter(class_test=test, enrollment=enrollment).first()
            
            val = 0
            percentage = 0
            trend = "neutral"
            change_pct = 0

            if mark_entry:
                val = float(mark_entry.marks_obtained)
                max_m = float(test.max_marks)
                percentage = (val / max_m) * 100 if max_m > 0 else 0

                if previous_percentage is not None:
                    diff = percentage - previous_percentage
                    change_pct = diff
                    if diff > 0: trend = "increase"
                    elif diff < 0: trend = "decrease"
                    else: trend = "neutral"
                
                previous_percentage = percentage

            graph_data.append({
                "exam": test.test_name,
                "value": round(percentage, 1),
                "original_marks": val,
                "max_marks": float(test.max_marks),
                "change_percentage": round(change_pct, 1),
                "trend": trend
            })

        return Response({
            "type": "class_test_analytics",
            "student": target_student.student_name,
            "term": term_name,
            "subject": subject_name,
            "graph_data": graph_data
        })


class StudentClassTestSummaryView(APIView):
    """
    GET: Summary for student class tests in active academic year.
    Returns totals and subject-wise counts/averages.
    """
    permission_classes = [IsAuthenticated, IsStudent]

    def get(self, request):
        try:
            active_year = AcademicYear.objects.get(is_current=True)
        except AcademicYear.DoesNotExist:
            return Response({"error": "No Active Academic Year"}, 500)

        try:
            student = request.user.student_profile
        except AttributeError:
            return Response({"error": "Student profile not found"}, 404)

        try:
            enrollment = Enrollment.objects.get(student=student, academic_year=active_year)
        except Enrollment.DoesNotExist:
            return Response({"error": "Access Denied or Not Enrolled"}, 403)

        marks_qs = ClassTestMarks.objects.filter(
            enrollment=enrollment,
            class_test__academic_year=active_year
        ).select_related('class_test', 'class_test__subject').order_by('class_test__created_at')

        total_tests = marks_qs.count()
        if total_tests == 0:
            return Response({
                "type": "class_test_summary",
                "student": student.student_name,
                "total_tests": 0,
                "average_score": 0,
                "best_score": 0,
                "latest_change": 0,
                "subject_wise": []
            })

        percentages = []
        subject_map = {}

        for mark in marks_qs:
            max_marks = float(mark.class_test.max_marks or 0)
            obtained = float(mark.marks_obtained or 0)
            percentage = (obtained / max_marks * 100) if max_marks > 0 else 0
            percentages.append(percentage)

            subject_name = mark.class_test.subject.name
            entry = subject_map.get(subject_name)
            if not entry:
                entry = {"subject": subject_name, "total_tests": 0, "sum_pct": 0.0, "best_score": 0.0}
                subject_map[subject_name] = entry
            entry["total_tests"] += 1
            entry["sum_pct"] += percentage
            if percentage > entry["best_score"]:
                entry["best_score"] = percentage

        average_score = round(sum(percentages) / total_tests, 1)
        best_score = round(max(percentages), 1)
        latest_change = 0.0
        if len(percentages) >= 2:
            latest_change = round(percentages[-1] - percentages[-2], 1)

        subject_wise = []
        for entry in subject_map.values():
            avg = entry["sum_pct"] / entry["total_tests"] if entry["total_tests"] else 0
            subject_wise.append({
                "subject": entry["subject"],
                "total_tests": entry["total_tests"],
                "average_score": round(avg, 1),
                "best_score": round(entry["best_score"], 1)
            })

        subject_wise.sort(key=lambda x: x["subject"])

        return Response({
            "type": "class_test_summary",
            "student": student.student_name,
            "total_tests": total_tests,
            "average_score": average_score,
            "best_score": best_score,
            "latest_change": latest_change,
            "subject_wise": subject_wise
        })




#class teacheer dashborad view for overall class performance
class ClassTeacherDashboardView(APIView):
    """
    GET: Teacher Dashboard Overview (Health Check)
    - Avg: Overall Class Percentage for the Latest Completed Exam.
    - Trend: Increase/Decrease vs Previous Exam Class Average.
    - Risk: Count of unique students who failed 1 or more subjects in the Latest Exam.
      (Uses StudentMark.grade_point property logic dynamically)
    """
    permission_classes = [IsAuthenticated, IsTeacher]

    def get(self, request):
        # 1. LOCK: Active Academic Year
        active_year = get_active_academic_year(request)
        if not active_year:
            return Response({"error": "Configuration Error: No Active Academic Year."}, 500)

        teacher = request.user.teacher_profile

        # 2. IDENTIFY CLASS: Find which section this teacher manages in Active Year
        ct_record = ClassTeacher.objects.filter(teacher=teacher, academic_year=active_year).select_related(
            'section__standard'
        ).first()

        my_section = ct_record.section if ct_record else None
        teacher_slots = TimetableSlot.objects.filter(
            teacher=teacher,
            academic_year=active_year
        ).select_related('section__standard', 'subject')

        if my_section:
            marks_scope = StudentMark.objects.filter(
                enrollment__section=my_section,
                enrollment__academic_year=active_year,
                schedule__academic_year=active_year
            )
            class_label = f"{my_section.standard.name} - {my_section.name}"
            view_mode = "class_teacher"
        else:
            slot_pairs = list(
                teacher_slots.values_list('section_id', 'subject_id').distinct()
            )

            if not slot_pairs:
                return Response({
                    "teacher": teacher.name,
                    "class": "Not Assigned",
                    "view": "subject_teacher",
                    "average": 0,
                    "trend": {"direction": "Neutral", "value": "0%"},
                    "at_risk": 0,
                    "message": "No timetable classes assigned for this teacher in the active year."
                })

            pair_query = Q()
            for section_id, subject_id in slot_pairs:
                pair_query |= Q(enrollment__section_id=section_id, subject_id=subject_id)

            marks_scope = StudentMark.objects.filter(
                pair_query,
                enrollment__academic_year=active_year,
                schedule__academic_year=active_year
            )

            section_labels = sorted({
                f"{slot.section.standard.name} - {slot.section.name}"
                for slot in teacher_slots
            })
            class_label = ", ".join(section_labels) if section_labels else "Assigned Subjects"
            view_mode = "subject_teacher"

        # 3. IDENTIFY EXAMS: Find exams that have marks for THIS section in THIS year
        taken_exam_ids = marks_scope.values_list('schedule__exam_type', flat=True).distinct()

        if not taken_exam_ids:
            return Response({
                "teacher": teacher.name,
                "class": class_label,
                "view": view_mode,
                "average": 0,
                "trend": {"direction": "Neutral", "value": "0%"},
                "at_risk": 0,
                "message": "No exams data found for your assigned classes yet."
            })

       # Sort exams by Academic Rank (Year-Safe Term Lock)
        completed_exams = ExamType.objects.filter(
            id__in=taken_exam_ids,
            term__academic_year=active_year
        ).select_related('term').order_by('term__rank', 'rank')

        # Pick Latest & Previous
        latest_exam = completed_exams.last()
        previous_exam = None
        if completed_exams.count() > 1:
            previous_exam = completed_exams[completed_exams.count() - 2]

        # --- HELPER: Calculate Overall Class Average ---
        def get_class_aggregate(exam_obj):
            if not exam_obj: return 0.0
            stats = marks_scope.filter(
                schedule__exam_type=exam_obj
            ).aggregate(
                total_obtained=Sum('marks_obtained'),
                total_max=Sum('total_marks')
            )
            obt = stats['total_obtained'] or 0
            max_m = stats['total_max'] or 0
            return round((obt / max_m * 100), 1) if max_m > 0 else 0.0

        # 4. CALCULATE METRICS
        latest_avg = get_class_aggregate(latest_exam)
        prev_avg = get_class_aggregate(previous_exam)

        # Trend Logic
        diff = latest_avg - prev_avg
        trend_label = "Neutral"
        trend_val_str = "0%"
        
        if previous_exam:
            sign = "+" if diff > 0 else "" 
            trend_val_str = f"{sign}{round(diff, 1)}%"
            if diff > 0: trend_label = "Increase"
            elif diff < 0: trend_label = "Decrease"
        
        # Risk Logic (PYTHON BASED - Safe & Consistent)
        # Fetch all marks for the latest exam
        latest_marks = marks_scope.filter(
            schedule__exam_type=latest_exam
        ).select_related('enrollment')

        # Identify unique students who have at least one 'F'
        failed_students = set()
        for mark in latest_marks:
            # This calls your @property in models.py
            if mark.grade_point == 'F':
                failed_students.add(mark.enrollment.id)
        
        risk_count = len(failed_students)

        return Response({
            "teacher": teacher.name,
            "class": class_label,
            "view": view_mode,
            "latest_exam": f"{latest_exam.name} ({latest_exam.term.name})",
            "average": latest_avg,
            "trend": {
                "direction": trend_label,
                "value": trend_val_str
            },
            "at_risk": risk_count
        })
