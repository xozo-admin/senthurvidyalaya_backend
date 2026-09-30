from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from django.shortcuts import get_object_or_404
from django.db.models import Q
from datetime import datetime, timedelta
# --- MODELS ---
from .models import TimetableSlot, Substitution
from teachers.models import Teacher, TeacherAllocation
from subjects.models import Subject
from academics.models import Standard, Section
from school.models import AcademicYear 

# --- SERIALIZERS ---
from .serializers import (
    TimetableCreateSerializer, 
    SubstitutionInputSerializer,
    MyTimetableSerializer,
    ClassBreakSerializer,
    TimetableAutoGenerateSerializer
)
from .services.auto_generator import TimetableAutoGenerator, MultiSectionTimetableAutoGenerator
from .scope import (
    get_scoped_active_year,
    get_scoped_section,
    get_scoped_standard,
    get_scoped_year_by_name,
    scoped_sections,
    scoped_standards,
    scoped_teachers,
)

# --- PERMISSIONS ---
from teachers.permissions import IsTeacher
from staff.permissions import IsOwnerAdminOrAdminStaff
from .models import TimetableSlot, Substitution, ClassBreak  # <--- Add ClassBreak here

# ==========================================
#      HELPER: VALIDATE ALLOCATION 
# ==========================================
def validate_teacher_allocation(teacher, subject, section, academic_year):
    """
    Checks if a teacher is actually APPROVED to teach this subject 
    to this Standard in this Academic Year.
    """
    if not teacher: 
        return True, "" 

    is_allocated = TeacherAllocation.objects.filter(
        academic_year=academic_year,
        teacher=teacher,
        subject=subject,
        standard=section.standard
    ).exists()

    if not is_allocated:
        return False, f"{teacher.name} is not allocated to teach {subject.name} for Class {section.standard.name} in {academic_year.name}."
    
    return True, ""

##helper function to inject breaks

def inject_breaks(timetable_list, standard, academic_year, specific_day=None):
    # Fetch breaks for this class
    breaks = ClassBreak.objects.filter(standard=standard, academic_year=academic_year)
    if not breaks.exists(): return timetable_list

    # Inject into the dictionary
    if isinstance(timetable_list, dict):
        days = [specific_day] if specific_day else timetable_list.keys()
        for day in days:
            if day not in timetable_list: continue
            
            for brk in breaks:
                timetable_list[day].append({
                    "period": 0, # Frontend can use 0 to identify non-academic slots
                    "time": f"{brk.start_time} - {brk.end_time}",
                    "subject": brk.name,   # e.g., "Lunch", "Interval"
                    "teacher": "BREAK",
                    "is_break": True
                })
            # Sort by time so Lunch appears in the middle
            timetable_list[day].sort(key=lambda x: datetime.strptime(x['time'].split(' - ')[0], '%H:%M:%S').time())
    return timetable_list

# ==========================================
#      1. ADMIN: Create Timetable (Active Year Only)
# ==========================================

class CreateTimetableView(APIView):
    permission_classes = [IsAuthenticated, IsOwnerAdminOrAdminStaff]

    def post(self, request):
        # 1. Creation is ALWAYS for the Active Year
        active_year = get_scoped_active_year(request)
        if not active_year:
            return Response({"error": "No Active Academic Year found."}, status=500)

        # 2. Normalize Data
        data_input = request.data if isinstance(request.data, list) else [request.data]
        
        # 3. Pre-Validation
        for item in data_input:
            t_id = item.get('teacher')
            s_id = item.get('subject')
            sec_id = item.get('section')

            if t_id and s_id and sec_id:
                try:
                    teacher = scoped_teachers(request).get(id=t_id)
                    subject = Subject.objects.get(id=s_id)
                    section = scoped_sections(request).get(id=sec_id)
                    
                    is_valid, msg = validate_teacher_allocation(teacher, subject, section, active_year)
                    if not is_valid:
                        return Response({"error": msg}, status=400)
                        
                except Exception:
                    continue 

        # 4. Save
        is_many = isinstance(request.data, list)
        serializer = TimetableCreateSerializer(data=request.data, many=is_many, context={"request": request})
        
        if serializer.is_valid():
            serializer.save(academic_year=active_year)
            return Response({"message": "Timetable created successfully for Active Year."}, status=201)
        
        return Response(serializer.errors, status=400)

# ==========================================
#      2. ADMIN: Manage Timetable (History Support)
# ==========================================
class AdminTimetableView(APIView):
    permission_classes = [IsAuthenticated, IsOwnerAdminOrAdminStaff]

    def get_target_year(self, request):
        """
        Determines which Academic Year to view.
        - If '?year=2024-2025' is provided -> View History.
        - Else -> View Active Year.
        """
        year_name = request.query_params.get('year') # <--- UPDATED: Uses Name
        if year_name:
            return get_scoped_year_by_name(request, year_name)

        return get_scoped_active_year(request)

    def get(self, request):
        class_name = request.query_params.get('class')
        section_name = request.query_params.get('section')
        day_param = request.query_params.get('day')

        if not class_name or not section_name:
            return Response({"error": "Params 'class' and 'section' required"}, 400)

        # 1. Determine which Year to view (Active or History)
        target_year = self.get_target_year(request)
        if not target_year:
             return Response({"error": "No active year configured and no year name provided."}, 500)

        try:
            standard = get_scoped_standard(request, class_name)
            section = get_scoped_section(request, standard, section_name)
        except (Standard.DoesNotExist, Section.DoesNotExist):
            return Response({"error": "Class or Section not found."}, status=404)

# 2. Filter Slots by Target Year
        slots = TimetableSlot.objects.filter(
            section=section, 
            academic_year=target_year
        ).order_by('day', 'period_no')
        
        if day_param:
            slots = slots.filter(day__iexact=day_param)

        # Fetch current week's substitutions
        today = datetime.now().date()
        start_of_week = today - timedelta(days=today.weekday())
        weekly_subs = Substitution.objects.filter(
            slot__section=section,
            date__range=[start_of_week, start_of_week + timedelta(days=5)]
        ).select_related('substitute_teacher', 'subject', 'slot')
        sub_map = {sub.slot.id: sub for sub in weekly_subs}

        # === NEW INITIALIZATION LOGIC ===

        # === NEW INITIALIZATION LOGIC ===
        # Initialize all days so breaks appear even if no classes exist
        days_of_week = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday']

        if day_param:
            # If viewing a specific day, initialize only that key (Handle casing: "monday" -> "Monday")
            target_day = day_param.title()
            timetable_data = {target_day: []}
        else:
            # Otherwise, start with all 6 days empty
            timetable_data = {d: [] for d in days_of_week}

        for slot in slots:
            if slot.day in timetable_data:
                is_sub = slot.id in sub_map
                sub_record = sub_map.get(slot.id)
                
                # Admin view likes to show the ID in brackets, so we keep that format
                if is_sub:
                    subject_name = sub_record.subject.name if sub_record.subject else slot.subject.name
                    teacher_name = f"{sub_record.substitute_teacher.name} ({sub_record.substitute_teacher.teacher_id})"
                else:
                    subject_name = slot.subject.name
                    teacher_name = f"{slot.teacher.name} ({slot.teacher.teacher_id})" if slot.teacher else "No Teacher"

                timetable_data[slot.day].append({
                    "period": slot.period_no,
                    "time": f"{slot.start_time} - {slot.end_time}",
                    "subject": subject_name, 
                    "teacher": teacher_name,
                    "is_substitution": is_sub,
                    "is_combined_class": bool(slot.combined_group)
                })
        # ================================

        
        # === ADD THIS LINE ===
        # This ensures Admin sees breaks even if no classes exist yet
        timetable_data = inject_breaks(timetable_data, standard, target_year)
        # =====================

        return Response({
            "status": 200,
            "class": f"{class_name} - {section_name}",
            "year": target_year.name,  # Confirms which year we are viewing
            "timetable": timetable_data
        }, status=status.HTTP_200_OK)

    # PUT: Update (Strictly Active Year)
    def put(self, request):
        active_year = get_scoped_active_year(request)
        if not active_year:
            return Response({"error": "No Active Academic Year found."}, 500)

        serializer = TimetableCreateSerializer(data=request.data, context={"request": request})
        if serializer.is_valid():
            # Validate Allocation
            t_obj = serializer.validated_data.get('teacher')
            s_obj = serializer.validated_data.get('subject')
            sec_obj = serializer.validated_data.get('section')
            
            is_valid, msg = validate_teacher_allocation(t_obj, s_obj, sec_obj, active_year)
            if not is_valid:
                return Response({"error": msg}, status=400)

            # Save with Active Year
            serializer.save(academic_year=active_year)
            return Response({
                "status": 200,
                "message": "Timetable updated",
                "data": request.data
            }, status=status.HTTP_200_OK)
        return Response(serializer.errors, status=400)

    # DELETE: Remove slot (Strictly Active Year)
    def delete(self, request):
        class_name = request.query_params.get('class')
        section_name = request.query_params.get('section')
        day_param = request.query_params.get('day')
        period_param = request.query_params.get('period')

        if not all([class_name, section_name, day_param]):
            return Response({"error": "Params 'class', 'section', 'day' required"}, 400)

        try:
            active_year = get_scoped_active_year(request)
            if not active_year:
                raise AcademicYear.DoesNotExist
            standard = get_scoped_standard(request, class_name)
            section = get_scoped_section(request, standard, section_name)
        except (Standard.DoesNotExist, Section.DoesNotExist):
            return Response({"error": "Class/Section not found"}, 404)
        except AcademicYear.DoesNotExist:
             return Response({"error": "No active year configured."}, 500)

        # Filter by Active Year only
        slots_to_delete = TimetableSlot.objects.filter(
            section=section, 
            day__iexact=day_param,
            academic_year=active_year
        )

        if period_param:
            slots_to_delete = slots_to_delete.filter(period_no=period_param)
            msg = f"Period {period_param} deleted for {active_year.name}."
        else:
            msg = f"Entire day {day_param} deleted for {active_year.name}."

        deleted_count, _ = slots_to_delete.delete()

        if deleted_count == 0:
            return Response({"message": "No records found."}, status=404)

        return Response({"status": 200, "message": msg}, status=status.HTTP_200_OK)


# ==========================================
#      3. STUDENT & TEACHER VIEWS (Locked to Active Year)
# ==========================================
class StudentTimetableView(APIView):
    permission_classes = [IsAuthenticated] 
    def get(self, request):
        try:
            student = request.user.student_profile
            # Teachers/Students are LOCKED to Active Year (No history view)
            active_year = get_scoped_active_year(request)
            if not active_year:
                raise AcademicYear.DoesNotExist
        except:
            return Response({"error": "Profile or Year error"}, 404)
            
        if not student.section: return Response({"error": "No class assigned"}, 400)

        # 1. Capture the day parameter
        day_param = request.query_params.get('day')

        # 2. Filter slots
        slots = TimetableSlot.objects.filter(
            section=student.section,
            academic_year=active_year
        ).order_by('day', 'period_no')
        
        if day_param:
            slots = slots.filter(day__iexact=day_param)

            # Fetch current week's substitutions
        today = datetime.now().date()
        start_of_week = today - timedelta(days=today.weekday())
        weekly_subs = Substitution.objects.filter(
            slot__section=student.section,
            date__range=[start_of_week, start_of_week + timedelta(days=5)]
        ).select_related('substitute_teacher', 'subject', 'slot')
        sub_map = {sub.slot.id: sub for sub in weekly_subs}

        # 3. Initialize Dictionary (Fixes empty days)
        days_of_week = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday']
        if day_param:
            target_day = day_param.title()
            timetable_data = {target_day: []}
        else:
            timetable_data = {d: [] for d in days_of_week}

        for slot in slots:
            if slot.day in timetable_data:
                is_sub = slot.id in sub_map
                sub_record = sub_map.get(slot.id)
                
                subject_name = sub_record.subject.name if (is_sub and sub_record.subject) else slot.subject.name
                teacher_name = sub_record.substitute_teacher.name if is_sub else (slot.teacher.name if slot.teacher else "No Teacher")

                timetable_data[slot.day].append({
                    "period": slot.period_no,
                    "time": f"{slot.start_time} - {slot.end_time}",
                    "subject": subject_name,
                    "teacher": teacher_name,
                    "is_substitution": is_sub
                })

        # Inject Breaks
        timetable_data = inject_breaks(timetable_data, student.section.standard, active_year)
        # ===================================
        return Response({"class": f"{student.section.standard.name}-{student.section.name}", "timetable": timetable_data})


class TeacherClassTimetableView(APIView):
    """
    GET: View Class Timetable for the Class Teacher (Active Year Only).
    """
    permission_classes = [IsAuthenticated, IsTeacher]

    def get(self, request):
        try:
            teacher = request.user.teacher_profile
            active_year = get_scoped_active_year(request)
            if not active_year:
                raise AcademicYear.DoesNotExist
        except:
            return Response({"error": "Teacher profile not found"}, 404)

        # Check 'ClassTeacher' table for ACTIVE YEAR
        from academics.models import ClassTeacher
        ct_record = ClassTeacher.objects.filter(teacher=teacher, academic_year=active_year).first()
        if not ct_record:
             return Response({"error": "You are not a Class Teacher for this year."}, 403)
             
        section = ct_record.section
        date_param = request.query_params.get('date')

        # SCENARIO 1: SPECIFIC DATE (With Subs)
        if date_param:
            try:
                date_obj = datetime.strptime(date_param, '%Y-%m-%d').date()
                day_name = date_obj.strftime('%A')
            except ValueError:
                return Response({"error": "Invalid date format"}, 400)

            original_slots = TimetableSlot.objects.filter(
                section=section, 
                day__iexact=day_name,
                academic_year=active_year
            ).order_by('period_no')

            active_subs = Substitution.objects.filter(
                slot__section=section,
                date=date_obj
            ).select_related('substitute_teacher', 'subject', 'slot')

            sub_map = {sub.slot.id: sub for sub in active_subs}
            daily_data = []

            for slot in original_slots:
                teacher_name = slot.teacher.name if slot.teacher else "No Teacher"
                subject_name = slot.subject.name
                is_sub = False

                if slot.id in sub_map:
                    sub_record = sub_map[slot.id]
                    teacher_name = sub_record.substitute_teacher.name
                    if sub_record.subject:
                        subject_name = sub_record.subject.name
                    is_sub = True

                daily_data.append({
                    "period": slot.period_no,
                    "time": f"{slot.start_time} - {slot.end_time}",
                    "subject": subject_name,
                    "teacher": teacher_name,
                    "is_substitution": is_sub,
                    "original_slot_id": slot.id 
                })

            return Response({
                "status": 200,
                "view_type": "Daily",
                "date": date_param,
                "class": f"{section.standard.name}-{section.name}",
                "timetable": daily_data
            })

        # SCENARIO 2: WEEKLY TEMPLATE
        # SCENARIO 2: WEEKLY TEMPLATE
        else:
            # 1. Capture the day parameter
            day_param = request.query_params.get('day')
            
            # 2. Filter Slots
            slots = TimetableSlot.objects.filter(section=section, academic_year=active_year).order_by('day', 'period_no')
            
            if day_param:
                slots = slots.filter(day__iexact=day_param)

                # Fetch current week's substitutions
            today = datetime.now().date()
            start_of_week = today - timedelta(days=today.weekday())
            weekly_subs = Substitution.objects.filter(
                slot__section=section,
                date__range=[start_of_week, start_of_week + timedelta(days=5)]
            ).select_related('substitute_teacher', 'subject', 'slot')
            sub_map = {sub.slot.id: sub for sub in weekly_subs}

            # 3. Initialize Dictionary (Fixes empty days)
            days_of_week = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday']
            if day_param:
                target_day = day_param.title()
                timetable_data = {target_day: []}
            else:
                timetable_data = {d: [] for d in days_of_week}

            for slot in slots:
                if slot.day in timetable_data:
                    is_sub = slot.id in sub_map
                    sub_record = sub_map.get(slot.id)
                    
                    subject_name = sub_record.subject.name if (is_sub and sub_record.subject) else slot.subject.name
                    teacher_name = sub_record.substitute_teacher.name if is_sub else (slot.teacher.name if slot.teacher else "No Teacher")

                    timetable_data[slot.day].append({
                        "period": slot.period_no,
                        "time": f"{slot.start_time} - {slot.end_time}",
                        "subject": subject_name,
                        "teacher": teacher_name,
                        "original_slot_id": slot.id,
                        "is_substitution": is_sub
                    })

            # Inject breaks
            timetable_data = inject_breaks(timetable_data, section.standard, active_year)
            # ===================================

            return Response({
                "status": 200,
                "view_type": "Weekly Template",
                "timetable": timetable_data
            })


class TeacherMyTimetableView(APIView):
    """
    GET: View the logged-in teacher's OWN timetable for ACTIVE YEAR.
    """
    permission_classes = [IsAuthenticated, IsTeacher]

    def get(self, request):
        try:
            teacher_profile = request.user.teacher_profile
            active_year = get_scoped_active_year(request)
            if not active_year:
                raise AcademicYear.DoesNotExist
        except:
            return Response({"error": "Profile error"}, status=404)

        # Filter by Teacher AND Active Year
        slots = TimetableSlot.objects.filter(
            teacher=teacher_profile,
            academic_year=active_year
        )

        day_param = request.query_params.get('day')
        if day_param:
            slots = slots.filter(day__iexact=day_param)

        slots = slots.order_by('day', 'period_no')

        serializer = MyTimetableSerializer(slots, many=True)
        grouped_timetable = {}
        day_order = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday']

        for d in day_order:
            if day_param and d.lower() != day_param.lower(): continue
            grouped_timetable[d] = []

        for item in serializer.data:
            d = item['day']
            if d in grouped_timetable:
                grouped_timetable[d].append(item)

        return Response({
            "status": 200,
            "teacher": teacher_profile.name,
            "year": active_year.name,
            "timetable": grouped_timetable
        }, status=status.HTTP_200_OK)


class FreeTeachersForSubstitutionView(APIView):
    """
    GET: Find free teachers in ACTIVE YEAR.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        date_str = request.query_params.get('date')
        period_no = request.query_params.get('period')

        if not date_str or not period_no:
            return Response({"error": "Params required"}, 400)

        try:
            user_type = getattr(request.user, 'user_type', None)
            is_admin_staff = (
                user_type == 'staff'
                and hasattr(request.user, 'staff_profile')
                and getattr(request.user.staff_profile, 'role', None) == 'admin_staff'
            )
            if user_type != 'teacher' and user_type not in ('admin', 'super_admin') and not is_admin_staff:
                return Response({"error": "Only Teacher/Admin can access this API"}, 403)

            active_year = get_scoped_active_year(request)
            if not active_year:
                raise AcademicYear.DoesNotExist

            if user_type == 'teacher':
                teacher = request.user.teacher_profile
                from academics.models import ClassTeacher
                ct = ClassTeacher.objects.filter(teacher=teacher, academic_year=active_year).first()
                if not ct:
                    return Response({"error": "You are not a Class Teacher"}, 403)
                target_standard = ct.section.standard
            else:
                class_name = request.query_params.get('class_name') or request.query_params.get('class')
                section_name = request.query_params.get('section')
                if not class_name or not section_name:
                    return Response({"error": "Admin must provide class_name and section"}, 400)

                standard = get_scoped_standard(request, class_name)
                section = get_scoped_section(request, standard, section_name)
                target_standard = section.standard

            date_obj = datetime.strptime(date_str, '%Y-%m-%d').date()
            day_name = date_obj.strftime('%A')
        except Exception as e:
            return Response({"error": str(e)}, 400)

        # 1. Find BUSY Teachers (in Active Year slots)
        busy_in_timetable = TimetableSlot.objects.filter(
            day__iexact=day_name, 
            period_no=period_no,
            academic_year=active_year
        ).values_list('teacher_id', flat=True)

        busy_in_subs = Substitution.objects.filter(
            date=date_obj,
            slot__period_no=period_no
        ).values_list('substitute_teacher_id', flat=True)

        busy_ids = set(list(busy_in_timetable) + list(busy_in_subs))

        # 2. Find PRIORITY Teachers (Allocated in Active Year)
        priority_teacher_ids = TeacherAllocation.objects.filter(
            subject__standard=target_standard,
            academic_year=active_year
        ).values_list('teacher_id', flat=True).distinct()

        teacher_pool = scoped_teachers(request)
        priority_teachers = teacher_pool.filter(id__in=priority_teacher_ids).exclude(id__in=busy_ids)
        fallback_teachers = teacher_pool.exclude(id__in=priority_teacher_ids).exclude(id__in=busy_ids)

        if priority_teachers.exists():
            final_list = priority_teachers
            message = f"Showing available teachers for {target_standard.name}."
            is_fallback = False
        else:
            final_list = fallback_teachers
            message = "No subject teachers available. Showing others."
            is_fallback = True

        data = []
        for t in final_list:
            subject_name = "General"
            if not is_fallback:
                alloc = TeacherAllocation.objects.filter(
                    teacher=t, 
                    subject__standard=target_standard,
                    academic_year=active_year
                ).first()
                if alloc: subject_name = alloc.subject.name
            
            data.append({
                "teacher_id": t.teacher_id,
                "name": t.name,
                "department": t.department,
                "default_subject": subject_name
            })

        return Response({
            "status": 200,
            "message": message,
            "teachers": data
        })


class AssignSubstitutionView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        date_str = request.query_params.get('date')
        if not date_str: return Response({"error": "Date required"}, 400)

        try:
            user_type = getattr(request.user, 'user_type', None)
            is_admin_staff = (
                user_type == 'staff'
                and hasattr(request.user, 'staff_profile')
                and getattr(request.user.staff_profile, 'role', None) == 'admin_staff'
            )
            if user_type != 'teacher' and user_type not in ('admin', 'super_admin') and not is_admin_staff:
                return Response({"error": "Only Teacher/Admin can assign substitution"}, 403)

            active_year = get_scoped_active_year(request)
            if not active_year:
                raise AcademicYear.DoesNotExist

            if user_type == 'teacher':
                teacher = request.user.teacher_profile
                from academics.models import ClassTeacher
                ct = ClassTeacher.objects.filter(teacher=teacher, academic_year=active_year).first()
                if not ct:
                    return Response({"error": "You are not a Class Teacher"}, 403)
                my_section = ct.section
            else:
                class_name = request.query_params.get('class_name') or request.query_params.get('class')
                section_name = request.query_params.get('section')
                if not class_name or not section_name:
                    return Response({"error": "Admin must provide class_name and section"}, 400)

                standard = get_scoped_standard(request, class_name)
                my_section = get_scoped_section(request, standard, section_name)
            
            serializer = SubstitutionInputSerializer(data=request.data)
            if not serializer.is_valid(): return Response(serializer.errors, 400)

            data = serializer.validated_data
            date_obj = datetime.strptime(date_str, '%Y-%m-%d').date()
            day_name = date_obj.strftime('%A')
            
            slot = TimetableSlot.objects.get(
                section=my_section,
                day__iexact=day_name,
                period_no=data['period_no'],
                academic_year=active_year
            )
            
            new_teacher = scoped_teachers(request).get(teacher_id=data['teacher_id'])
            new_subject = Subject.objects.get(name__iexact=data['subject'], standard=my_section.standard)
            
            Substitution.objects.update_or_create(
                slot=slot,
                date=date_obj,
                defaults={
                    'substitute_teacher': new_teacher,
                    'subject': new_subject
                }
            )
            
            return Response({"status": 200, "message": "Substitution Assigned."})

        except Exception as e:
            return Response({"error": str(e)}, 400)


class RecentSubstitutionsView(APIView):
    """
    GET: List recent substitutions for the class teacher (Active Year Only).
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        try:
            user_type = getattr(request.user, 'user_type', None)
            is_admin_staff = (
                user_type == 'staff'
                and hasattr(request.user, 'staff_profile')
                and getattr(request.user.staff_profile, 'role', None) == 'admin_staff'
            )
            if user_type != 'teacher' and user_type not in ('admin', 'super_admin') and not is_admin_staff:
                return Response({"error": "Only Teacher/Admin can access this API"}, 403)

            active_year = get_scoped_active_year(request)
            if not active_year:
                raise AcademicYear.DoesNotExist

            if user_type == 'teacher':
                teacher = request.user.teacher_profile
                from academics.models import ClassTeacher
                ct = ClassTeacher.objects.filter(teacher=teacher, academic_year=active_year).first()
                if not ct:
                    return Response({"error": "You are not a Class Teacher"}, 403)
                target_section = ct.section
            else:
                class_name = request.query_params.get('class_name') or request.query_params.get('class')
                section_name = request.query_params.get('section')
                if not class_name or not section_name:
                    return Response({"error": "Admin must provide class_name and section"}, 400)
                standard = get_scoped_standard(request, class_name)
                target_section = get_scoped_section(request, standard, section_name)

            limit = int(request.query_params.get('limit', 10))
            if limit <= 0:
                limit = 10
        except Exception as e:
            return Response({"error": str(e)}, 400)

        substitutions = (
            Substitution.objects
            .filter(slot__section=target_section, slot__academic_year=active_year)
            .select_related('slot', 'subject', 'substitute_teacher', 'slot__section', 'slot__subject')
            .order_by('-date', '-created_at')[:limit]
        )

        data = []
        for sub in substitutions:
            subject_name = sub.subject.name if sub.subject else sub.slot.subject.name
            data.append({
                "date": sub.date.isoformat(),
                "period_no": sub.slot.period_no,
                "subject": subject_name,
                "substitute_teacher": sub.substitute_teacher.name,
                "substitute_teacher_id": sub.substitute_teacher.teacher_id,
                "status": "Assigned"
            })

        return Response({
            "status": 200,
            "substitutions": data
        })


class StudentCurrentNextClassView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        try:
            student = request.user.student_profile
            active_year = get_scoped_active_year(request)
            if not active_year:
                raise AcademicYear.DoesNotExist
        except:
            return Response({"error": "Profile error"}, 404)
        
        if not student.section: return Response({"error": "No class assigned"}, 400)

        now = datetime.now()
        current_time = now.time()
        day_name = now.strftime('%A')

        today_slots = TimetableSlot.objects.filter(
            section=student.section, 
            day__iexact=day_name,
            academic_year=active_year
        ).order_by('start_time')

        # CHECK IF CURRENTLY BREAK
        now_break = ClassBreak.objects.filter(
            standard=student.section.standard,
            academic_year=active_year,
            start_time__lte=current_time,
            end_time__gte=current_time
        ).first()

        if now_break:
            current_class_data = {
                "subject": now_break.name, # "Lunch"
                "time": f"{now_break.start_time} - {now_break.end_time}",
                "teacher": "",
                "is_break": True
            }

        active_subs = Substitution.objects.filter(
            slot__section=student.section,
            date=now.date()
        ).select_related('substitute_teacher', 'subject', 'slot')
        
        sub_map = {sub.slot.id: sub for sub in active_subs}

        def format_slot(slot):
            if slot.id in sub_map:
                sub = sub_map[slot.id]
                subj_name = sub.subject.name if sub.subject else slot.subject.name
                teach_name = sub.substitute_teacher.name
                is_sub = True
            else:
                subj_name = slot.subject.name
                teach_name = slot.teacher.name if slot.teacher else "No Teacher"
                is_sub = False

            return {
                "subject": subj_name,
                "time": f"{slot.start_time.strftime('%I:%M %p')} - {slot.end_time.strftime('%I:%M %p')}",
                "teacher": teach_name,
                "period": slot.period_no,
                "is_substitution": is_sub
            }

        current_class_data = None
        next_class_data = None

        for i, slot in enumerate(today_slots):
            if slot.start_time <= current_time <= slot.end_time:
                current_class_data = format_slot(slot)
                if i + 1 < len(today_slots):
                    next_class_data = format_slot(today_slots[i + 1])
                break
            elif current_time < slot.start_time:
                next_class_data = format_slot(slot)
                break
        
        return Response({
            "status": 200,
            "day": day_name,
            "current_class": current_class_data,
            "next_class": next_class_data
        })



# timetable/views.py

from .models import ClassBreak
from .serializers import ClassBreakSerializer

class ClassBreakView(APIView):
    permission_classes = [IsAuthenticated, IsOwnerAdminOrAdminStaff]

    def get(self, request):
        class_name = request.query_params.get('class')
        try:
            active_year = get_scoped_active_year(request)
            if not active_year:
                raise AcademicYear.DoesNotExist
            standard = get_scoped_standard(request, class_name)
            breaks = ClassBreak.objects.filter(academic_year=active_year, standard=standard)
            serializer = ClassBreakSerializer(breaks, many=True)
            return Response(serializer.data)
        except:
            return Response([], status=200)

    def post(self, request):
        try:
            active_year = get_scoped_active_year(request)
            if not active_year:
                raise AcademicYear.DoesNotExist
        except AcademicYear.DoesNotExist:
            return Response({"error": "No Active Year"}, 400)

        serializer = ClassBreakSerializer(data=request.data, context={"request": request})
        if serializer.is_valid():
            start = serializer.validated_data['start_time']
            end = serializer.validated_data['end_time']
            std = serializer.validated_data['standard']

            # VALIDATION: Check if this NEW Break overlaps with EXISTING Breaks
            overlap = ClassBreak.objects.filter(
                academic_year=active_year, standard=std,
                start_time__lt=end, end_time__gt=start
            ).exists()
            
            if overlap:
                return Response({"error": "This time overlaps with an existing break."}, 400)
            
            # VALIDATION: Check if this NEW Break overlaps with EXISTING Timetable Slots
            # If classes are already scheduled here, warn the admin.
            slot_overlap = TimetableSlot.objects.filter(
                academic_year=active_year, 
                section__standard=std,
                start_time__lt=end, end_time__gt=start
            ).exists()

            if slot_overlap:
                return Response({"error": "Cannot create break. Classes are already scheduled during this time. Delete them first."}, 400)

            serializer.save(academic_year=active_year)
            return Response({"message": f"{serializer.validated_data['name']} added successfully."}, 201)
        return Response(serializer.errors, 400)


    def put(self, request):
        # 1. Get the Break ID to edit
        pk = request.data.get('id')
        if not pk: 
            return Response({"error": "ID required for update"}, 400)

        try:
            active_year = get_scoped_active_year(request)
            if not active_year:
                raise AcademicYear.DoesNotExist
            # Ensure we only edit breaks in the Active Year
            break_obj = ClassBreak.objects.get(pk=pk, academic_year=active_year, standard__in=scoped_standards(request))
        except (ClassBreak.DoesNotExist, AcademicYear.DoesNotExist):
            return Response({"error": "Break not found or Year inactive"}, 404)

        # 2. Serialize & Validate
        # Note: Frontend must send 'class_name' again so the serializer can validate the standard
        serializer = ClassBreakSerializer(break_obj, data=request.data, context={"request": request})
        
        if serializer.is_valid():
            start = serializer.validated_data['start_time']
            end = serializer.validated_data['end_time']
            std = serializer.validated_data['standard']

            # VALIDATION A: Check overlap with OTHER Breaks (Exclude Self)
            overlap = ClassBreak.objects.filter(
                academic_year=active_year, standard=std,
                start_time__lt=end, end_time__gt=start
            ).exclude(id=pk).exists() # <--- CRITICAL: Don't check against self
            
            if overlap:
                return Response({"error": "This time overlaps with another break."}, 400)
            
            # VALIDATION B: Check overlap with Timetable Slots
            # (We cannot move Lunch to 10am if there is a Maths class at 10am)
            slot_overlap = TimetableSlot.objects.filter(
                academic_year=active_year, 
                section__standard=std,
                start_time__lt=end, end_time__gt=start
            ).exists()

            if slot_overlap:
                return Response({"error": "Cannot update break. Classes are already scheduled during this new time."}, 400)

            serializer.save()
            return Response({"message": "Break updated successfully."}, 200)
        
        return Response(serializer.errors, 400)

    def delete(self, request):
        # Allow deleting by ID
        pk = request.query_params.get('id')
        if not pk: return Response({"error": "ID required"}, 400)
        
        get_object_or_404(ClassBreak, pk=pk, standard__in=scoped_standards(request)).delete()
        return Response({"message": "Break removed."})

##admin to view teacher timetable 

class AdminTeacherTimetableView(APIView):
    """
    GET: Admin views a specific teacher's weekly timetable.
    Requires '?teacher_id=...' in params.
    """
    permission_classes = [IsAuthenticated, IsOwnerAdminOrAdminStaff]

    def get_target_year(self, request):
        year_name = request.query_params.get('year')
        if year_name:
            return get_scoped_year_by_name(request, year_name)
        try:
            return get_scoped_active_year(request)
        except AcademicYear.DoesNotExist:
            return None

    def get(self, request):
        teacher_id_param = request.query_params.get('teacher_id')
        day_param = request.query_params.get('day')

        if not teacher_id_param:
            return Response({"error": "Param 'teacher_id' is required"}, status=400)

        target_year = self.get_target_year(request)
        if not target_year:
             return Response({"error": "No active year configured."}, status=500)

        # 1. Find the Teacher
        try:
            teacher = scoped_teachers(request).get(teacher_id=teacher_id_param)
        except Teacher.DoesNotExist:
            return Response({"error": f"Teacher with ID '{teacher_id_param}' not found."}, status=404)

        # 2. Filter Slots by Teacher and Year
        slots = TimetableSlot.objects.filter(
            teacher=teacher,
            academic_year=target_year
        ).order_by('day', 'period_no')
        
        if day_param:
            slots = slots.filter(day__iexact=day_param)

        # 3. Initialize Dictionary (Monday to Saturday)
        days_of_week = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday']

        if day_param:
            target_day = day_param.title()
            timetable_data = {target_day: []}
        else:
            timetable_data = {d: [] for d in days_of_week}

        # 4. Fill the Dictionary
        for slot in slots:
            if slot.day in timetable_data:
                timetable_data[slot.day].append({
                    "period": slot.period_no,
                    "time": f"{slot.start_time} - {slot.end_time}",
                    # For a teacher, the most important thing is knowing WHICH class to go to
                    "class": f"{slot.section.standard.name} - {slot.section.name}", 
                    "subject": slot.subject.name
                })

        return Response({
            "status": 200,
            "teacher": f"{teacher.name} ({teacher.teacher_id})",
            "department": teacher.department,
            "year": target_year.name,
            "timetable": timetable_data
        }, status=status.HTTP_200_OK)


class AutoGenerateTimetableView(APIView):
    """
    POST: Auto-generate timetable for class-section using CP-SAT.
    """
    permission_classes = [IsAuthenticated, IsOwnerAdminOrAdminStaff]

    def post(self, request):
        active_year = get_scoped_active_year(request)
        if not active_year:
            return Response({"error": "No Active Academic Year found."}, status=500)

        serializer = TimetableAutoGenerateSerializer(data=request.data, context={"request": request})
        if not serializer.is_valid():
            return Response(serializer.errors, status=400)

        data = serializer.validated_data
        try:
            if "section_objs" in data:
                generator = MultiSectionTimetableAutoGenerator(
                    academic_year=active_year,
                    standard=data["standard_obj"],
                    sections=data["section_objs"],
                    days=data["days"],
                    day_period_counts=data["day_period_counts"],
                    max_subject_periods_per_day=data["max_subject_periods_per_day"],
                    period_duration_minutes=data["period_duration_minutes"],
                    first_period_start_time=data["first_period_start_time"],
                    subject_periods=data.get("subject_periods"),
                    section_subject_teacher_ids=data.get("section_subject_teacher_ids"),
                    support_combined_class=data.get("support_combined_class", False),
                    overwrite_existing=data.get("overwrite_existing", False),
                )
            else:
                generator = TimetableAutoGenerator(
                    academic_year=active_year,
                    standard=data["standard_obj"],
                    section=data["section_obj"],
                    days=data["days"],
                    day_period_counts=data["day_period_counts"],
                    max_subject_periods_per_day=data["max_subject_periods_per_day"],
                    period_duration_minutes=data["period_duration_minutes"],
                    first_period_start_time=data["first_period_start_time"],
                    subject_periods=data.get("subject_periods"),
                    section_subject_teacher_ids=data.get("section_subject_teacher_ids"),
                    overwrite_existing=data.get("overwrite_existing", False),
                )
            result = generator.generate_and_save()

            return Response(
                {
                    "status": 200,
                    "message": "Timetable auto-generated successfully.",
                    "data": result,
                },
                status=status.HTTP_200_OK,
            )
        except ValueError as exc:
            return Response({"error": str(exc)}, status=400)
        except RuntimeError as exc:
            return Response({"error": str(exc)}, status=500)
        except Exception as exc:
            return Response({"error": f"Auto-generation failed: {str(exc)}"}, status=500)
