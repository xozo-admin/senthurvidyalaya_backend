from decimal import Decimal, InvalidOperation

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from django.shortcuts import get_object_or_404

# --- MODELS ---
from .models import Teacher, TeacherAllocation, TEACHER_PERMISSION_GROUPS, TEACHER_PERMISSION_LABELS
from students.models import Student, Enrollment  # <--- IMPORT ENROLLMENT
from academics.models import Standard, Section, ClassTeacher # <--- Added ClassTeacher
from subjects.models import Subject
from school.models import AcademicYear  
from school.tenant import get_active_academic_year, scope_queryset_for_user
from timetable.models import TimetableSlot # <--- NEW: For Test Permission Checks
from exams.models import ExamTerm, ClassTest, ClassTestMarks

# --- SERIALIZERS ---
from .serializers import TeacherProfileSerializer, SimpleTeacherSerializer, ClassTestSerializer, ClassTestMarkViewSerializer, TeacherRolePermissionSerializer
from students.serializers import StudentSerializer

# --- PERMISSIONS ---
from .permissions import IsTeacher 
from schooladmin.permissions import IsAdmin
from django.db import transaction

# ==========================================
# 1. TEACHER PROFILE & DETAILS (UNCHANGED)
# ==========================================

class TeacherProfileView(APIView):
    permission_classes = [IsAuthenticated, IsTeacher]
    def get(self, request):
        teacher = get_object_or_404(Teacher, user=request.user)
        serializer = TeacherProfileSerializer(teacher)
        return Response({"status": 200, "data": serializer.data}, status=status.HTTP_200_OK)

class AdminTeacherListView(APIView):
    permission_classes = [IsAuthenticated, IsAdmin]
    def get(self, request):
        teachers = scope_queryset_for_user(Teacher.objects.all(), request)
        serializer = TeacherProfileSerializer(teachers, many=True)
        return Response({"status": 200, "count": teachers.count(), "data": serializer.data}, status=status.HTTP_200_OK)

class AdminTeacherDropdownView(APIView):
    permission_classes = [IsAuthenticated, IsAdmin]
    def get(self, request):
        teachers = scope_queryset_for_user(Teacher.objects.all(), request)
        serializer = SimpleTeacherSerializer(teachers, many=True)
        return Response({"status": 200, "count": teachers.count(), "data": serializer.data}, status=status.HTTP_200_OK)


class TeacherRolePermissionView(APIView):
    permission_classes = [IsAuthenticated]

    def _can_admin_manage(self, request):
        return request.user.user_type in ('admin', 'super_admin')

    def get(self, request):
        if request.user.user_type == 'teacher':
            teacher = get_object_or_404(Teacher, user=request.user)
            serializer = TeacherRolePermissionSerializer(teacher)
            return Response({"status": 200, "data": serializer.data}, status=status.HTTP_200_OK)

        if not self._can_admin_manage(request):
            return Response({"error": "Only teachers and admins can access role permissions."}, status=403)

        teacher_id = request.query_params.get('teacher_id')
        teachers = scope_queryset_for_user(Teacher.objects.all(), request).order_by('name')
        if teacher_id:
            teacher = get_object_or_404(teachers, teacher_id=teacher_id)
            serializer = TeacherRolePermissionSerializer(teacher)
            return Response({"status": 200, "data": serializer.data}, status=status.HTTP_200_OK)

        serializer = TeacherRolePermissionSerializer(teachers, many=True)
        return Response({
            "status": 200,
            "count": teachers.count(),
            "permission_groups": TEACHER_PERMISSION_GROUPS,
            "permission_labels": TEACHER_PERMISSION_LABELS,
            "data": serializer.data,
        }, status=status.HTTP_200_OK)

    def put(self, request):
        if not self._can_admin_manage(request):
            return Response({"error": "Only admins can update teacher role permissions."}, status=403)

        teacher_id = request.data.get('teacher_id') or request.query_params.get('teacher_id')
        if not teacher_id:
            return Response({"error": "teacher_id is required."}, status=400)

        teacher = get_object_or_404(scope_queryset_for_user(Teacher.objects.all(), request), teacher_id=teacher_id)
        serializer = TeacherRolePermissionSerializer(teacher, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save()
            return Response({
                "status": 200,
                "message": f"Role permissions updated for {teacher.name}.",
                "data": serializer.data,
            }, status=status.HTTP_200_OK)
        return Response(serializer.errors, status=400)

class TeacherDetailView(APIView):
    permission_classes = [IsAuthenticated, IsAdmin]
    def get(self, request):
        target_id = request.query_params.get('teacher_id')
        if not target_id: return Response({"error": "teacher_id parameter is required"}, status=400)
        teacher = get_object_or_404(scope_queryset_for_user(Teacher.objects.all(), request), teacher_id=target_id) 
        serializer = TeacherProfileSerializer(teacher)
        return Response({"status": 200, "data": serializer.data}, status=status.HTTP_200_OK)

    def put(self, request):
        target_id = request.query_params.get('teacher_id')
        if not target_id: return Response({"error": "teacher_id parameter is required"}, status=400)
        teacher = get_object_or_404(scope_queryset_for_user(Teacher.objects.all(), request), teacher_id=target_id)
        serializer = TeacherProfileSerializer(teacher, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save()  
            return Response({"status": 200, "message": "Teacher updated successfully", "data": serializer.data}, status=status.HTTP_200_OK)
        return Response(serializer.errors, status=400)


# ==========================================
# 2. TEACHER ALLOCATION (SETUP PHASE - UNCHANGED)
# ==========================================

class AssignTeacherSubjectView(APIView):
    """
    POST: Assign Subject to Teacher (Qualification Only) for the ACTIVE YEAR.
    """
    permission_classes = [IsAuthenticated, IsAdmin]

    def post(self, request):
        active_year = get_active_academic_year(request)
        if not active_year:
            return Response({"error": "No Active Academic Year found. Please set one in Settings."}, 500)

        data_list = request.data if isinstance(request.data, list) else [request.data]
        results = []

        for entry in data_list:
            t_id = entry.get('teacher_id')
            sub_name = entry.get('subject_name') or entry.get('subject')
            cls_list = entry.get('classes') or [entry.get('class')]

            if not t_id or not sub_name or not cls_list:
                results.append({"error": "Missing fields", "input": entry})
                continue

            try:
                teacher = scope_queryset_for_user(Teacher.objects.all(), request).get(teacher_id=t_id)
            except Teacher.DoesNotExist:
                results.append({"error": f"Teacher {t_id} not found"})
                continue

            for cls_name in cls_list:
                if not cls_name: continue
                try:
                    std_obj = scope_queryset_for_user(Standard.objects.all(), request).get(name=cls_name)
                    subject = Subject.objects.get(name__iexact=sub_name, standard=std_obj)
                    
                    # Create Year-Safe Allocation
                    TeacherAllocation.objects.get_or_create(
                        academic_year=active_year,
                        school=std_obj.school,
                        teacher=teacher,
                        subject=subject,
                        standard=std_obj
                    )
                    results.append(f"Qualification added: {teacher.name} -> {sub_name} (Class {cls_name}) [{active_year.name}]")
                    
                except Standard.DoesNotExist:
                    results.append({"error": f"Class '{cls_name}' not found"})
                except Subject.DoesNotExist:
                    results.append({"error": f"Subject '{sub_name}' not found for Class {cls_name}"})
                except Exception as e:
                    results.append({"error": str(e)})

        return Response({
            "status": 200,
            "message": "Teacher qualifications updated for the Active Year.",
            "year": active_year.name,
            "details": results
        }, status=status.HTTP_200_OK)
        
# ==========================================
# 3. CLASS MANAGEMENT (REFACTORED for Safety)
# ==========================================

class TeacherStudentDetailView(APIView):
    """
    View Student Details.
    - Security: Checks if the Teacher is the CLASS TEACHER for the student's current enrollment.
    - Input: ?student_id=ST1001
    """
    permission_classes = [IsAuthenticated, IsTeacher]

    def get(self, request):
        target_id = request.query_params.get('student_id')
        if not target_id: return Response({"error": "Please provide 'student_id' parameter."}, status=400)

        try:
            teacher_profile = request.user.teacher_profile
            active_year = AcademicYear.objects.get(is_current=True)
        except Exception:
            return Response({"error": "Teacher profile or Active Year not found."}, status=404)

        # 1. Find if I am a Class Teacher this year
        ct_record = ClassTeacher.objects.filter(
            teacher=teacher_profile, 
            academic_year=active_year
        ).first()

        if not ct_record:
            return Response({"error": "You are not assigned as a Class Teacher for this academic year."}, status=403)

        # 2. Find the Student's Enrollment for this year
        try:
            enrollment = Enrollment.objects.get(
                student__student_id=target_id,
                academic_year=active_year
            )
        except Enrollment.DoesNotExist:
            return Response({"error": f"Student '{target_id}' is not enrolled in the active academic year."}, status=404)

        # 3. Verify Match (Is the student in MY section?)
        if enrollment.section != ct_record.section:
            return Response({"error": "Access Denied. This student is not in your assigned class."}, status=status.HTTP_403_FORBIDDEN)

        # 4. Return Student Profile Data
        serializer = StudentSerializer(enrollment.student)
        return Response({"status": 200, "data": serializer.data})


# ==========================================
# 4. MANAGE CLASS TESTS (REFACTORED for Timetable)
# ==========================================
class ClassTestManagerView(APIView):
    permission_classes = [IsAuthenticated, IsTeacher]

    def post(self, request):
        teacher = request.user.teacher_profile
        try:
            active_year = AcademicYear.objects.get(is_current=True)
        except:
            return Response({"error": "No Active Year"}, 500)

        # 1. Inputs
        class_name = request.data.get('class')
        section_name = request.data.get('section')
        subject_name = request.data.get('subject')
        term_name = request.data.get('term')
        test_name = request.data.get('test_name')
        max_marks = request.data.get('max_marks')

        if not all([class_name, section_name, subject_name, term_name, test_name, max_marks]):
            return Response({"error": "All fields are required."}, 400)

        # 2. Validation & YEAR-SAFE Permission Check
        # 2. Validation & YEAR-SAFE Permission Check
        try:
            section = Section.objects.get(name=section_name, standard__name=class_name)
            subject = Subject.objects.get(name__iexact=subject_name, standard__name=class_name)
            term = ExamTerm.objects.get(name__iexact=term_name, academic_year=active_year) # <--- YEAR LOCK

            # CRITICAL UPDATE: Check TIMETABLE SLOT instead of Allocation
            # Logic: "Does this teacher have a slot for THIS Subject in THIS Section?"
            is_allocated = TimetableSlot.objects.filter(
                academic_year=active_year,
                teacher=teacher,
                section=section,
                subject=subject
            ).exists()

            if not is_allocated:
                return Response({"error": f"Permission Denied: You are not scheduled to teach {subject_name} in Class {class_name}-{section_name}."}, 403)

        except (Section.DoesNotExist, Subject.DoesNotExist, ExamTerm.DoesNotExist):
            return Response({"error": "Invalid Class, Section, Subject, or Term"}, 404)

        # 3. Create Test (Linked to Active Year)
        try:
            test = ClassTest.objects.create(
                academic_year=active_year,
                term=term,
                teacher=teacher,
                section=section,
                subject=subject,
                test_name=test_name,
                max_marks=max_marks
            )
            return Response({"message": "Class Test Created", "test_id": test.id}, 201)
        except Exception as e:
            return Response({"error": f"Creation Failed (Duplicate name?): {str(e)}"}, 400)

    def get(self, request):
        teacher = request.user.teacher_profile
        try:
            active_year = AcademicYear.objects.get(is_current=True)
        except: return Response({"error": "No Active Year"}, 500)

        term_filter = request.query_params.get('term') 

        # Filter tests by Active Year
        tests = ClassTest.objects.filter(teacher=teacher, academic_year=active_year)
        
        if term_filter:
            tests = tests.filter(term__name__iexact=term_filter)

        serializer = ClassTestSerializer(tests, many=True)
        return Response({"status": 200, "data": serializer.data})

    def put(self, request):
        test_id = request.data.get('test_id')
        new_max_marks = request.data.get('max_marks')
        new_test_name = request.data.get('test_name')  
        
        try:
            active_year = AcademicYear.objects.get(is_current=True)
            test = ClassTest.objects.get(id=test_id, teacher=request.user.teacher_profile, academic_year=active_year)
            
            if new_max_marks:
                test.max_marks = new_max_marks
            
            if new_test_name:
                test.test_name = new_test_name 
            
            test.save()
            return Response({"message": "Test updated successfully."})
            
        except ClassTest.DoesNotExist:
            return Response({"error": "Test not found or permission denied."}, 404)
        except Exception as e:
            return Response({"error": f"Update failed (Duplicate name?): {str(e)}"}, 400)


    def delete(self, request):
        test_id = request.query_params.get('test_id')
        try:
            active_year = AcademicYear.objects.get(is_current=True)
            test = ClassTest.objects.get(id=test_id, teacher=request.user.teacher_profile, academic_year=active_year)
            test.delete()
            return Response({"message": "Test and all marks deleted."})
        except ClassTest.DoesNotExist:
            return Response({"error": "Test not found"}, 404)


# ==========================================
# 5. MANAGE MARKS (UNCHANGED - Already uses Enrollment)
# ==========================================
class ClassTestMarksView(APIView):
    permission_classes = [IsAuthenticated, IsTeacher]

    def post(self, request):
        teacher = request.user.teacher_profile
        test_id = request.data.get('test_id')
        marks_data = request.data.get('marks_data', [])

        try:
            active_year = AcademicYear.objects.get(is_current=True)
            test_obj = ClassTest.objects.get(
                id=test_id, 
                teacher=teacher, 
                academic_year=active_year
            )
        except (AcademicYear.DoesNotExist, ClassTest.DoesNotExist):
            return Response({"error": "Test not found, year inactive, or permission denied."}, 404)

        if not marks_data: return Response({"error": "No marks provided"}, 400)

        processed_count = 0
        errors = []

        with transaction.atomic():
            for entry in marks_data:
                if not isinstance(entry, dict):
                    errors.append("Invalid row format")
                    continue

                s_id = entry.get('student_id')
                raw_marks = entry.get('marks')

                if not s_id:
                    errors.append("Student ID is required")
                    continue

                try:
                    marks = Decimal(str(raw_marks))
                except (InvalidOperation, TypeError, ValueError):
                    errors.append(f"Student {s_id}: Invalid marks")
                    continue

                if marks > test_obj.max_marks:
                    errors.append(f"Student {s_id}: Marks {marks} exceeds max {test_obj.max_marks}")
                    continue
                if marks < 0:
                    errors.append(f"Student {s_id}: Negative marks not allowed")
                    continue

                try:
                    # Logic: Find Enrollment in the Test's Section + Active Year
                    enrollment = Enrollment.objects.get(
                        student__student_id=s_id,
                        section=test_obj.section,
                        academic_year=active_year
                    )
                    
                    ClassTestMarks.objects.update_or_create(
                        class_test=test_obj,
                        enrollment=enrollment,
                        defaults={'marks_obtained': marks}
                    )
                    processed_count += 1
                    
                except Enrollment.DoesNotExist:
                    errors.append(f"Student {s_id} is not enrolled in Class {test_obj.section} for {active_year.name}")

        return Response({
            "message": "Marks processing complete",
            "success_count": processed_count,
            "errors": errors
        })

    def get(self, request):
        test_id = request.query_params.get('test_id')
        
        if not test_id:
            return Response({"error": "test_id param is required"}, 400)

        try:
            active_year = AcademicYear.objects.get(is_current=True)
            test_obj = ClassTest.objects.get(id=test_id, teacher=request.user.teacher_profile, academic_year=active_year)
            
            marks = ClassTestMarks.objects.filter(class_test=test_obj)
            serializer = ClassTestMarkViewSerializer(marks, many=True)
            
            return Response({
                "test": test_obj.test_name,
                "max_marks": test_obj.max_marks,
                "count": marks.count(),
                "data": serializer.data
            })
        except ClassTest.DoesNotExist:
            return Response({"error": "Test not found or permission denied."}, 404)


    def put(self, request):
        teacher = request.user.teacher_profile
        test_id = request.data.get('test_id')
        student_id = request.data.get('student_id') 
        new_marks = request.data.get('marks')

        if not all([test_id, student_id, new_marks is not None]):
             return Response({"error": "test_id, student_id, and marks are required"}, 400)

        try:
            active_year = AcademicYear.objects.get(is_current=True)
            
            test_obj = ClassTest.objects.get(id=test_id, teacher=teacher, academic_year=active_year)

            enrollment = Enrollment.objects.get(
                student__student_id=student_id,
                academic_year=active_year,
                section=test_obj.section
            )

            mark_entry = ClassTestMarks.objects.get(class_test=test_obj, enrollment=enrollment)

            if float(new_marks) > float(test_obj.max_marks):
                return Response({"error": f"Marks cannot exceed {test_obj.max_marks}"}, 400)
            if float(new_marks) < 0:
                 return Response({"error": "Marks cannot be negative"}, 400)

            mark_entry.marks_obtained = new_marks
            mark_entry.save()
            
            return Response({
                "message": "Mark updated successfully", 
                "student": enrollment.student.student_name,
                "test": test_obj.test_name,
                "new_marks": new_marks
            })

        except Exception:
            return Response({"error": "Update failed. Check ID permissions."}, 500)

    def delete(self, request):
        teacher = request.user.teacher_profile
        test_id = request.query_params.get('test_id')
        student_id = request.query_params.get('student_id')
        reset_all = request.query_params.get('reset')

        if not test_id:
            return Response({"error": "test_id is required"}, 400)

        try:
            active_year = AcademicYear.objects.get(is_current=True)
            test_obj = ClassTest.objects.get(id=test_id, teacher=teacher, academic_year=active_year)
        except (AcademicYear.DoesNotExist, ClassTest.DoesNotExist):
            return Response({"error": "Test not found, year inactive, or permission denied"}, 404)

        if student_id:
            try:
                enrollment = Enrollment.objects.get(
                    student__student_id=student_id,
                    academic_year=active_year,
                    section=test_obj.section
                )
                mark_entry = ClassTestMarks.objects.get(class_test=test_obj, enrollment=enrollment)
                mark_entry.delete()
                
                return Response({
                    "message": f"Marks removed for {enrollment.student.student_name} in {test_obj.test_name}"
                })

            except (Enrollment.DoesNotExist, ClassTestMarks.DoesNotExist):
                return Response({"error": "Mark entry not found"}, 404)

        elif reset_all == 'true':
            count, _ = ClassTestMarks.objects.filter(class_test=test_obj).delete()
            return Response({"message": f"All marks cleared for {test_obj.test_name}. Deleted {count} entries."})

        else:
            return Response({"error": "Provide 'student_id' (to delete one) or 'reset=true' (to delete all)."}, 400)

## new goutham code 

class TeacherHandledSubjectsView(APIView):
    """
    Returns nested structure: Class -> Section -> List of Subjects
    """
    permission_classes = [IsAuthenticated, IsTeacher]

    def get(self, request):
        try:
            teacher_profile = request.user.teacher_profile
            active_year = AcademicYear.objects.get(is_current=True)
        except AcademicYear.DoesNotExist:
            return Response({"error": "Active Academic Year not set."}, 404)
        except Exception:
            return Response({"error": "Teacher profile not found."}, 404)

        # 1. Fetch the raw data from TimetableSlot
        handled_items = TimetableSlot.objects.filter(
            teacher=teacher_profile,
            academic_year=active_year
        ).values(
            'section__standard__name', 
            'section__name', 
            'subject__name'
        ).distinct()

        # 2. Build the Nested Structure
        # Target: { "10": { "A": ["Physics", "Maths"], "B": ["Science"] } }
        nested_data = {}

        for item in handled_items:
            class_name = item['section__standard__name']
            section_name = item['section__name']
            subject_name = item['subject__name']

            # Ensure the Class key exists
            if class_name not in nested_data:
                nested_data[class_name] = {}
            
            # Ensure the Section key exists within that Class
            if section_name not in nested_data[class_name]:
                nested_data[class_name][section_name] = []
            
            # Add subject if not already in the list (extra safety)
            if subject_name not in nested_data[class_name][section_name]:
                nested_data[class_name][section_name].append(subject_name)

        return Response({
            "status": 200,
            "academic_year": active_year.name,
            "data": nested_data
        }, status=status.HTTP_200_OK)

# # # #  SIVA BRO TEACHER VIEWS # # # #

class TeachersByClassView(APIView):
    """
    GET: Get all teacher allocations.
    Query Param: ?class_name=10 (optional)
    """
    permission_classes = [IsAuthenticated]
    def get(self, request):
        class_name = request.query_params.get('class_name')
        
        # Get all teacher allocations with necessary joins
        allocations = TeacherAllocation.objects.select_related(
            'teacher', 'subject', 'subject__standard', 'academic_year'
        )
        
        # If class filter is provided
        if class_name:
            allocations = allocations.filter(subject_standard_name=class_name)
        
        # Get current academic year for additional filtering (optional)
        try:
            active_year = AcademicYear.objects.get(is_current=True)
        except AcademicYear.DoesNotExist:
            active_year = None
        
        # Format response
        response_data = []
        seen_combinations = set()  # To avoid duplicates
        
        for alloc in allocations:
            # Skip if not in active year (if you want only current year)
            if active_year and alloc.academic_year != active_year:
                continue
            
            # Create a unique key for this teacher-subject-class combination
            key = f"{alloc.teacher.teacher_id}-{alloc.subject.name}-{alloc.subject.standard.name}-{alloc.academic_year.id}"
            
            if key not in seen_combinations:
                seen_combinations.add(key)
                
                # Now we need to find which sections this teacher teaches this subject to
                # We'll check TimetableSlot to find sections
                sections = []
                
                if active_year:
                    # Find all timetable slots for this teacher, subject, and standard in current year
                    timetable_slots = TimetableSlot.objects.filter(
                        academic_year=active_year,
                        teacher=alloc.teacher,
                        subject=alloc.subject,
                        section__standard=alloc.subject.standard
                    ).select_related('section')
                    
                    sections = list(set([slot.section.name for slot in timetable_slots]))
                
                response_data.append({
                    "teacher": alloc.teacher.id,
                    "teacher_id": alloc.teacher.teacher_id,
                    "teacher_name": alloc.teacher.name,
                    "subject": alloc.subject.name,
                    "class": alloc.subject.standard.name,
                    "academic_year": alloc.academic_year.name,
                    "sections": sorted(sections)  # Sort alphabetically
                })
        
        return Response(response_data, status=200)
    

class AllTeachersAllocationsView(APIView):
    """
    GET: Get ALL subject allocations for ALL teachers.
    No parameters required - returns comprehensive data for admin dashboard.
    """
    permission_classes = [IsAuthenticated]
    
    def get(self, request):
        try:
            active_year = AcademicYear.objects.get(is_current=True)
        except AcademicYear.DoesNotExist:
            return Response({"error": "No active academic year found"}, status=400)
        
        # Get all allocations for current academic year
        allocations = TeacherAllocation.objects.filter(
            academic_year=active_year
        ).select_related(
            'teacher', 
            'subject', 
            'standard'
        ).order_by('teacher__name', 'standard__name')  # FIXED: Use double underscore to reference related fields
        
        # Group by teacher for better organization
        teacher_map = {}
        
        for alloc in allocations:
            teacher_id = alloc.teacher.teacher_id
            teacher_name = alloc.teacher.name
            
            if teacher_id not in teacher_map:
                teacher_map[teacher_id] = {
                    "teacher_id": teacher_id,
                    "teacher_name": teacher_name,
                    "teacher_department": alloc.teacher.department or "",
                    "teacher_email": alloc.teacher.email or "",
                    "total_subjects": 0,
                    "total_classes": set(),
                    "subjects": []
                }
            
            # Get sections this teacher teaches this subject to (from TimetableSlot)
            sections = TimetableSlot.objects.filter(
                academic_year=active_year,
                teacher=alloc.teacher,
                subject=alloc.subject,
                section__standard=alloc.standard
            ).values_list('section__name', flat=True).distinct()
            
            sections_list = list(sections)
            
            # Check if this subject already exists for this teacher
            existing_subject = None
            for subject in teacher_map[teacher_id]["subjects"]:
                if subject["subject_name"] == alloc.subject.name:
                    existing_subject = subject
                    break
            
            if existing_subject:
                # Add class and sections to existing subject
                class_name = alloc.standard.name
                if class_name not in existing_subject["classes"]:
                    existing_subject["classes"].append(class_name)
                    existing_subject["sections"][class_name] = sections_list
                else:
                    # Merge sections for existing class
                    existing_sections = existing_subject["sections"].get(class_name, [])
                    existing_sections.extend(sections_list)
                    existing_subject["sections"][class_name] = list(set(existing_sections))
            else:
                # Create new subject entry
                subject_data = {
                    "subject_name": alloc.subject.name,
                    "subject_code": alloc.subject.subject_code or "",
                    "classes": [alloc.standard.name],
                    "sections": {alloc.standard.name: sections_list}
                }
                teacher_map[teacher_id]["subjects"].append(subject_data)
                teacher_map[teacher_id]["total_subjects"] += 1
            
            # Add to total classes set
            teacher_map[teacher_id]["total_classes"].add(alloc.standard.name)
        
        # Convert to list format and calculate totals
        result = []
        for teacher_data in teacher_map.values():
            # Convert set to sorted list for classes
            teacher_data["total_classes"] = sorted(list(teacher_data["total_classes"]))
            
            # Format sections for each subject
            for subject in teacher_data["subjects"]:
                # Sort classes
                subject["classes"] = sorted(subject["classes"])
                
                # Format sections as list of strings for easier display
                formatted_sections = []
                for cls in subject["classes"]:
                    sections_list = subject["sections"].get(cls, [])
                    if sections_list:
                        formatted_sections.append(f"Class {cls}: {', '.join(sorted(sections_list))}")
                    else:
                        # If no timetable slots found, show "No sections allocated"
                        formatted_sections.append(f"Class {cls}: No timetable allocation")
                
                subject["sections_display"] = formatted_sections
                # Remove the original sections dict to avoid duplication
                del subject["sections"]
            
            result.append(teacher_data)
        
        # Sort by teacher name
        result.sort(key=lambda x: x["teacher_name"])
        
        # Calculate summary statistics
        summary = {
            "total_teachers": len(result),
            "total_allocations": allocations.count(),
            "total_unique_subjects": Subject.objects.filter(
                teacherallocation__academic_year=active_year
            ).distinct().count(),
            "total_classes": Standard.objects.count(),
            "academic_year": active_year.name
        }
        
        return Response({
            "status": 200,
            "summary": summary,
            "allocations": result
        }, status=200)
    
class TeacherClassSubjectsView(APIView):
    """
    GET: Returns all subjects for the teacher's assigned class (if they are a class teacher)
    URL: /api/teacher/my-class-subjects/
    """
    permission_classes = [IsAuthenticated, IsTeacher]
    
    def get(self, request):
        # 1. Get the teacher's profile
        try:
            teacher = request.user.teacher_profile
        except:
            return Response({"error": "Teacher profile not found"}, status=404)
        
        # 2. Check if teacher is assigned as a Class Teacher for the current academic year
        try:
            active_year = AcademicYear.objects.get(is_current=True)
        except AcademicYear.DoesNotExist:
            return Response({"error": "No active academic year found"}, status=400)
        
        # Check if teacher has a ClassTeacher record for the active year
        class_teacher_record = ClassTeacher.objects.filter(
            teacher=teacher,
            academic_year=active_year
        ).first()
        
        if not class_teacher_record:
            return Response({
                "error": "You are not assigned as a Class Teacher. Only Class Teachers have specific class subjects."
            }, status=403)
        
        # 3. Get the assigned section and standard from the ClassTeacher record
        section = class_teacher_record.section
        standard = section.standard
        
        # 4. Fetch all subjects for this class
        subjects = Subject.objects.filter(standard=standard).order_by('name')
        
        # 5. Serialize the data
        subjects_data = []
        for subject in subjects:
            subjects_data.append({
                "subject_id": subject.id,
                "subject_name": subject.name,
                "subject_code": subject.subject_code
            })
        
        return Response({
            "status": 200,
            "teacher_name": teacher.name,
            "assigned_class": {
                "class_name": standard.name,
                "section_name": section.name,
                "full_name": f"{standard.name}-{section.name}"
            },
            "subjects": subjects_data,
            "count": len(subjects_data)
        }, status=200)
    
class TeacherSubjectAllocationsView(APIView):
    """
    GET: Get all subject allocations for a specific teacher.
    Query Param: ?teacher_id=TCH001
    """
    permission_classes = [IsAuthenticated]
    
    def get(self, request):
        teacher_id = request.query_params.get('teacher_id')
        if not teacher_id:
            return Response({"error": "teacher_id parameter is required"}, 400)
        try:
            teacher = Teacher.objects.get(teacher_id=teacher_id)
        except Teacher.DoesNotExist:
            return Response({"error": "Teacher not found"}, 404)
        
        # Get current academic year
        try:
            active_year = AcademicYear.objects.get(is_current=True)
        except AcademicYear.DoesNotExist:
            return Response({"error": "No active academic year found"}, 400)
        
        # Get all allocations for this teacher in current year
        allocations = TeacherAllocation.objects.filter(
            teacher=teacher, 
            academic_year=active_year
        ).select_related('subject', 'standard')
        
        # Group by subject
        subject_map = {}
        for alloc in allocations:
            # Get sections from TimetableSlot for this teacher, subject, and standard
            sections = TimetableSlot.objects.filter(
                academic_year=active_year,
                teacher=teacher,
                subject=alloc.subject,
                section__standard=alloc.standard
            ).values_list('section__name', flat=True).distinct()
            
            sections_list = list(sections)
            
            if alloc.subject.name not in subject_map:
                subject_map[alloc.subject.name] = {
                    "subject_name": alloc.subject.name,
                    "subject_code": alloc.subject.subject_code,
                    "classes": set([alloc.standard.name]),  # Use set to avoid duplicates
                    "sections": sections_list
                }
            else:
                subject_map[alloc.subject.name]["classes"].add(alloc.standard.name)
                # Merge sections
                existing_sections = subject_map[alloc.subject.name]["sections"]
                existing_sections.extend(sections_list)
                subject_map[alloc.subject.name]["sections"] = list(set(existing_sections))
        
        # Convert sets to lists and format the response
        result = []
        for subject_data in subject_map.values():
            # Format sections for each class
            formatted_sections = []
            for cls in sorted(list(subject_data["classes"])):
                # Filter sections for this specific class - FIXED HERE
                class_sections = []
                for slot in TimetableSlot.objects.filter(
                    academic_year=active_year,
                    teacher=teacher,
                    subject__name=subject_data["subject_name"],
                    section__standard__name=cls  # <-- FIXED: section__standard__name instead of section_standard_name
                ).distinct():
                    class_sections.append(slot.section.name)
                
                if class_sections:
                    formatted_sections.append(f"Class {cls}: {', '.join(sorted(set(class_sections)))}")
            
            result.append({
                "subject_name": subject_data["subject_name"],
                "subject_code": subject_data["subject_code"],
                "classes": sorted(list(subject_data["classes"])),
                "sections": subject_data["sections"],
                "sections_display": formatted_sections
            })
        
        return Response({
            "status": 200,
            "teacher_id": teacher_id,
            "teacher_name": teacher.name,
            "academic_year": active_year.name,
            "allocations": result
        }, status=200)
