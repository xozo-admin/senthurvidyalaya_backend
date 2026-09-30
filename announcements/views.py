from collections import defaultdict
from datetime import date, timedelta

from django.conf import settings

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from teachers.permissions import IsTeacher
from .models import Announcement, TeacherAnnouncement
from .serializers import (
    AnnouncementCreateSerializer, 
    AnnouncementListSerializer, 
    TeacherAnnouncementSerializer
)
from academics.models import Standard, Section, ClassTeacher
from school.models import AcademicYear 
from teachers.models import TeacherAllocation
from timetable.models import TimetableSlot  # <--- KEY CHANGE: Import Timetable

# --- STAFF IMPORTS ---
from django.shortcuts import get_object_or_404
from django.db.models import Q, Count
from staff.permissions import IsAdmin, IsStaff
from .models import StaffAnnouncement, CommonAnnouncement
from django.contrib.auth import get_user_model
from .serializers import StaffAnnouncementSerializer, CommonAnnouncementSerializer, AnnouncementListWithDateSerializer, CommonAnnouncementWithDateSerializer
from notifications.utils import send_notification_to_users
User = get_user_model()


def _school_user_q(school):
    if not school:
        return Q()
    return (
        Q(user_type='super_admin')
        | Q(adminprofile__school=school)
        | Q(teacher_profile__school=school)
        | Q(student_profile__school=school)
        | Q(staff_profile__school=school)
    )


def _active_school_users(school=None, user_types=None):
    queryset = User.objects.filter(is_active=True)
    if user_types:
        queryset = queryset.filter(user_type__in=user_types)
    if school:
        queryset = queryset.filter(_school_user_q(school))
    return queryset.distinct()


def _announcement_preview(text, limit=120):
    value = str(text or '').strip()
    if len(value) <= limit:
        return value
    return f"{value[:limit].rstrip()}..."


def _parse_pagination(query_params, default_page=1, default_page_size=10, max_page_size=100):
    try:
        page = int(query_params.get('page', default_page))
        page_size = int(query_params.get('page_size', default_page_size))
    except (TypeError, ValueError):
        return None, None, Response({"error": "Invalid page or page_size"}, status=400)

    if page < 1:
        page = 1
    if page_size < 1:
        page_size = default_page_size
    page_size = min(page_size, max_page_size)
    return page, page_size, None


def _build_pagination_payload(page, page_size, total):
    total_pages = (total + page_size - 1) // page_size if total > 0 else 1
    if page > total_pages:
        page = total_pages
    start = (page - 1) * page_size
    end = start + page_size
    return page, start, end, {
        "page": page,
        "page_size": page_size,
        "total": total,
        "total_pages": total_pages,
        "has_next": page < total_pages,
        "has_previous": page > 1,
    }

# ==========================================
# HELPER: STRICT PERMISSION CHECK (TIMETABLE-BASED)
# ==========================================
def check_teacher_permission(user, section, academic_year):
    """
    Returns True if the teacher is:
    1. The Class Teacher for this Section.
    2. OR has a Timetable Slot for this Section (Subject Teacher).
    """
    try:
        teacher = user.teacher_profile
        
        # 1. Check Class Teacher (Highest Priority)
        is_class_teacher = ClassTeacher.objects.filter(
            teacher=teacher,
            section=section,
            academic_year=academic_year
        ).exists()
        
        if is_class_teacher: return True

        # 2. Check Timetable Slot (Strict Subject Teacher Check)
        # "Does this teacher have ANY slot in the timetable for this section?"
        has_timetable_slot = TimetableSlot.objects.filter(
            academic_year=academic_year,
            teacher=teacher,
            section=section
        ).exists()

        return has_timetable_slot

    except Exception:
        return False

# ==========================================
# 1. POST: Create Announcement (Teacher -> Student)
# ==========================================
class CreateAnnouncementView(APIView):
    permission_classes = [IsAuthenticated, IsTeacher]

    def post(self, request):
        try:
            active_year = AcademicYear.objects.get(is_current=True)
        except AcademicYear.DoesNotExist:
            return Response({"error": "No Active Academic Year found."}, 500)
        
        # --- RETURN TO OLD LOGIC ---
        # No manual "section_id" check here. 
        # The Serializer will accept 'class_name' & 'section_name' 
        # and perform the Timetable permission check internally.
        
        serializer = AnnouncementCreateSerializer(data=request.data, context={'request': request})
        
        if serializer.is_valid():
            announcement = serializer.save(academic_year=active_year)
            try:
                target_recipients = User.objects.filter(
                    user_type='student',
                    student_profile__enrollments__section=announcement.section,
                    student_profile__enrollments__academic_year=active_year,
                    student_profile__enrollments__is_active=True,
                    is_active=True,
                ).distinct()
                subject_label = announcement.subject.name if announcement.subject else "Class"
                send_notification_to_users(
                    users=target_recipients,
                    sender=request.user,
                    title=f"New Update: {subject_label}",
                    message=(
                        f"{request.user.teacher_profile.name} posted "
                        f"{_announcement_preview(announcement.description)}"
                    ),
                    notif_type="Update",
                    data={
                        "screen": "student_updates",
                        "permission": "updates",
                        "class": announcement.section.standard.name,
                        "section": announcement.section.name,
                    },
                )
            except Exception as e:
                print(f"Notification Error: {e}")
            
            return Response({
                "status": 200, 
                "message": "Announcement posted.",
                "announcement_number": announcement.announcement_number
            })
        
        return Response(serializer.errors, status=400)
        
# ==========================================
# 2. GET: Notice Board (View All Teachers' Posts)
# ==========================================
class NoticeBoardView(APIView):
    permission_classes = [IsAuthenticated, IsTeacher] 

    def get(self, request):
        try:
            active_year = AcademicYear.objects.get(is_current=True)
        except AcademicYear.DoesNotExist:
            return Response({"error": "No Active Academic Year found."}, 500)

        teacher = request.user.teacher_profile
        date_param = request.query_params.get('date')

        if not date_param:
            return Response({"error": "Param 'date' is required"}, 400)

        # --- LOGIC: Resolve Target Section ---
        target_section = None
        
        class_name = request.query_params.get('class')
        section_name = request.query_params.get('section')

        # Mode A: Subject Teacher (Specific Class Requested)
        if class_name and section_name:
            try:
                target_section = Section.objects.get(standard__name=class_name, name=section_name)
            except Section.DoesNotExist:
                return Response({"error": "Invalid Class/Section"}, 404)
        
        # Mode B: Class Teacher (No Class Requested -> Find My Class)
        else:
            ct_record = ClassTeacher.objects.filter(
                teacher=teacher, 
                academic_year=active_year
            ).first()

            if ct_record:
                target_section = ct_record.section
            else:
                return Response({"error": "You are not a Class Teacher. Please provide 'class' and 'section' params to view Subject posts."}, 400)

        # --- PERMISSION CHECK (Using Timetable) ---
        if not check_teacher_permission(request.user, target_section, active_year):
             return Response({"error": f"You are not allocated to Class {target_section.standard.name}-{target_section.name} in {active_year.name}"}, 403)

        # --- FETCH DATA ---
        announcements = Announcement.objects.filter(
            section=target_section,
            created_at__date=date_param,
            academic_year=active_year 
        ).order_by('-created_at')

        serializer = AnnouncementListSerializer(announcements, many=True)
        
        return Response({
            "status": 200,
            "viewing_class": f"{target_section.standard.name}-{target_section.name}",
            "year": active_year.name,
            "date": date_param,
            "mode": "Subject View" if class_name else "Class Teacher View",
            "data": serializer.data
        })

# ==========================================
# 3. GET / PUT / DELETE: Manage MY Posts Only
# ==========================================
class MyPostView(APIView):
    permission_classes = [IsAuthenticated, IsTeacher]

    def get_object(self, request):
        try:
            active_year = AcademicYear.objects.get(is_current=True)
        except:
            return None

        date_str = request.query_params.get('date')
        number = request.query_params.get('number')

        if not date_str or not number:
            return None

        try:
            return Announcement.objects.get(
                teacher=request.user.teacher_profile,
                created_at__date=date_str,
                announcement_number=number,
                academic_year=active_year 
            )
        except Announcement.DoesNotExist:
            return None

    # GET: View MY posts
    def get(self, request):
        try:
            active_year = AcademicYear.objects.get(is_current=True)
        except:
            return Response({"error": "No Active Year"}, 500)

        # 1. Get Params
        date_str = request.query_params.get('date')
        class_name = request.query_params.get('class')    
        section_name = request.query_params.get('section') 
        scope = request.query_params.get('scope') 

        if not date_str:
            return Response({"error": "Param 'date' required"}, 400)

        # 2. Base Query
        my_posts = Announcement.objects.filter(
            teacher=request.user.teacher_profile,
            created_at__date=date_str,
            academic_year=active_year
        )

        # 3. Apply Filters
        
        # Scenario A: Specific Class -> SUBJECT TEACHER MODE
        if class_name and section_name:
            my_posts = my_posts.filter(
                section__standard__name=class_name,
                section__name=section_name,
                subject__isnull=False # STRICT: Subject posts only
            )
            
        # Scenario B: "My Class" -> CLASS TEACHER MODE
        elif scope == 'my_class':
            ct_record = ClassTeacher.objects.filter(
                teacher=request.user.teacher_profile,
                academic_year=active_year
            ).first()

            if not ct_record:
                return Response({"error": "You are not a Class Teacher for the current session."}, 400)
            
            my_posts = my_posts.filter(
                section=ct_record.section,
                subject__isnull=True # STRICT: General posts only
            )

        serializer = AnnouncementListSerializer(my_posts, many=True)
        return Response({
            "status": 200, 
            "year": active_year.name,
            "filter_applied": "My Class (General)" if scope == 'my_class' else "Subject View" if class_name else "All Activity",
            "my_posts": serializer.data
        })

    def put(self, request):
        announcement = self.get_object(request)
        if not announcement:
            return Response({"error": "Announcement not found"}, 404)

        announcement.description = request.data.get('description', announcement.description)
        announcement.announcement_type = request.data.get('announcement_type', announcement.announcement_type)
        announcement.save()
        
        return Response({"status": 200, "message": "Updated successfully."})

    def delete(self, request):
        announcement = self.get_object(request)
        if not announcement:
            return Response({"error": "Announcement not found"}, 404)
        
        announcement.delete()
        return Response({"status": 200, "message": "Deleted successfully."})

# ==========================================
# 4. STUDENT BOARD (Includes Admin Posts)
# ==========================================
class StudentAnnouncementView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        try:
            student = request.user.student_profile
            my_section = student.section
            active_year = AcademicYear.objects.get(is_current=True)
        except AttributeError:
             return Response({"error": "Access Denied. Students only."}, 403)
        except AcademicYear.DoesNotExist:
             return Response({"error": "No Active Academic Year found."}, 500)

        if not my_section:
             return Response({"error": "No class assigned."}, 400)

        req_source = request.query_params.get('source')   
        req_subject = request.query_params.get('subject') 
        req_date = request.query_params.get('date')       

        class_announcements = Announcement.objects.filter(
            section=my_section,
            academic_year=active_year
        )

        admin_announcements = CommonAnnouncement.objects.filter(
            academic_year=active_year
        )

        # Filters
        if req_source == 'admin':
            class_announcements = Announcement.objects.none()
        elif req_source == 'class_teacher':
            class_announcements = class_announcements.filter(subject__isnull=True)
            admin_announcements = CommonAnnouncement.objects.none()
        elif req_subject:
            class_announcements = class_announcements.filter(subject__name__iexact=req_subject)
            admin_announcements = CommonAnnouncement.objects.none()

        if req_date:
            class_announcements = class_announcements.filter(created_at__date=req_date)
            admin_announcements = admin_announcements.filter(date=req_date)

        class_serializer = AnnouncementListSerializer(class_announcements.order_by('-created_at'), many=True)
        admin_serializer = CommonAnnouncementSerializer(admin_announcements.order_by('-date'), many=True)

        return Response({
            "status": 200,
            "student": student.student_name,
            "class": f"{my_section.standard.name}-{my_section.name}",
            "year": active_year.name,
            "view_mode": req_source if req_source else "Overall Board",
            "count": class_announcements.count() + admin_announcements.count(),
            "class_announcements": class_serializer.data,
            "school_announcements": admin_serializer.data
        })


class StudentAnnouncementWithDateView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        try:
            student = request.user.student_profile
            my_section = student.section
            active_year = AcademicYear.objects.get(is_current=True)
        except AttributeError:
            return Response({"error": "Access Denied. Students only."}, 403)
        except AcademicYear.DoesNotExist:
            return Response({"error": "No Active Academic Year found."}, 500)

        if not my_section:
            return Response({"error": "No class assigned."}, 400)

        req_source = request.query_params.get('source')
        req_subject = request.query_params.get('subject')
        req_date = request.query_params.get('date')

        class_announcements = Announcement.objects.filter(
            section=my_section,
            academic_year=active_year
        )

        admin_announcements = CommonAnnouncement.objects.filter(
            academic_year=active_year
        )

        # Filters
        if req_source == 'admin':
            class_announcements = Announcement.objects.none()
        elif req_source == 'class_teacher':
            class_announcements = class_announcements.filter(subject__isnull=True)
            admin_announcements = CommonAnnouncement.objects.none()
        elif req_subject:
            class_announcements = class_announcements.filter(subject__name__iexact=req_subject)
            admin_announcements = CommonAnnouncement.objects.none()

        if req_date:
            class_announcements = class_announcements.filter(created_at__date=req_date)
            admin_announcements = admin_announcements.filter(date=req_date)

        class_serializer = AnnouncementListWithDateSerializer(class_announcements.order_by('-created_at'), many=True)
        admin_serializer = CommonAnnouncementWithDateSerializer(admin_announcements.order_by('-date'), many=True)

        return Response({
            "status": 200,
            "student": student.student_name,
            "class": f"{my_section.standard.name}-{my_section.name}",
            "year": active_year.name,
            "view_mode": req_source if req_source else "Overall Board",
            "count": class_announcements.count() + admin_announcements.count(),
            "class_announcements": class_serializer.data,
            "school_announcements": admin_serializer.data
        })

# =========================================================
#  5. ADMIN: STAFF ANNOUNCEMENTS (NEW FILTERS)
# =========================================================
class AdminStaffAnnouncementView(APIView):
    permission_classes = [IsAuthenticated, IsAdmin]

    def get(self, request):
        queryset = StaffAnnouncement.objects.all().order_by('-date')

        year_filter = request.query_params.get('year')
        if year_filter:
            queryset = queryset.filter(academic_year__name=year_filter)

        date_param = request.query_params.get('date')
        if date_param:
            queryset = queryset.filter(date=date_param)

        role_param = request.query_params.get('role')
        vis_param = request.query_params.get('visibility')

        # Logic: Filter by specific role OR 'ALL_STAFF'
        if role_param:
            queryset = queryset.filter(target_role__iexact=role_param, visibility='ROLE_SPECIFIC')
        elif vis_param == 'ALL_STAFF':
            queryset = queryset.filter(visibility='ALL_STAFF')

        serializer = StaffAnnouncementSerializer(queryset, many=True)
        return Response({
            "status": 200,
            "count": queryset.count(),
            "data": serializer.data
        })

    def post(self, request):
        try:
            active_year = AcademicYear.objects.get(is_current=True)
        except AcademicYear.DoesNotExist:
             return Response({"error": "No Active Year Configured"}, 500)

        serializer = StaffAnnouncementSerializer(data=request.data)
        if serializer.is_valid():
            announcement = serializer.save(academic_year=active_year)
            try:
                recipients = _active_school_users(active_year.school, ['staff'])
                if announcement.visibility == 'ROLE_SPECIFIC' and announcement.target_role:
                    recipients = recipients.filter(
                        staff_profile__role__iexact=announcement.target_role
                    )
                send_notification_to_users(
                    users=recipients,
                    sender=request.user,
                    title=f"Staff Update: {announcement.title}",
                    message=_announcement_preview(announcement.description),
                    notif_type="Staff Update",
                    data={
                        "screen": "staff_announcements",
                        "permission": "announcements",
                    },
                )
            except Exception as e:
                print(f"Notification Error: {e}")
            return Response({"status": 201, "message": "Created", "data": serializer.data}, status=201)
        return Response(serializer.errors, status=400)


class AdminStaffAnnouncementDetailView(APIView):
    permission_classes = [IsAuthenticated, IsAdmin]

    def get_object(self, pk):
        return get_object_or_404(StaffAnnouncement, pk=pk)

    def put(self, request, pk):
        announcement = self.get_object(pk)
        serializer = StaffAnnouncementSerializer(announcement, data=request.data, partial=True) 
        if serializer.is_valid():
            serializer.save()
            return Response({"status": 200, "message": "Updated", "data": serializer.data})
        return Response(serializer.errors, status=400)

    def delete(self, request, pk):
        announcement = self.get_object(pk)
        announcement.delete()
        return Response({"status": 200, "message": "Deleted"})


class AdminStaffAnnouncementPaginatedView(APIView):
    permission_classes = [IsAuthenticated, IsAdmin]

    def get(self, request):
        queryset = StaffAnnouncement.objects.all()

        year_filter = request.query_params.get('year')
        if year_filter:
            queryset = queryset.filter(academic_year__name=year_filter)

        date_param = request.query_params.get('date')
        if date_param:
            queryset = queryset.filter(date=date_param)

        role_param = request.query_params.get('role')
        vis_param = request.query_params.get('visibility')
        if role_param:
            queryset = queryset.filter(target_role__iexact=role_param, visibility='ROLE_SPECIFIC')
        elif vis_param == 'ALL_STAFF':
            queryset = queryset.filter(visibility='ALL_STAFF')

        search_text = (request.query_params.get('q') or '').strip()
        if search_text:
            queryset = queryset.filter(
                Q(title__icontains=search_text)
                | Q(description__icontains=search_text)
                | Q(target_role__icontains=search_text)
            )

        sort_by = (request.query_params.get('sort_by') or 'date').strip().lower()
        sort_dir = (request.query_params.get('sort_dir') or 'desc').strip().lower()
        sort_map = {
            'date': 'date',
            'title': 'title',
            'created_at': 'created_at',
        }
        order_field = sort_map.get(sort_by, 'date')
        if sort_dir != 'asc':
            order_field = f'-{order_field}'
        queryset = queryset.order_by(order_field, '-id')

        page, page_size, error_response = _parse_pagination(request.query_params)
        if error_response:
            return error_response

        total = queryset.count()
        page, start, end, pagination = _build_pagination_payload(page, page_size, total)
        serializer = StaffAnnouncementSerializer(queryset[start:end], many=True)
        return Response({
            "status": 200,
            "count": total,
            "data": serializer.data,
            "pagination": pagination,
        })


# =========================================================
#  6. STAFF: VIEW DASHBOARD (NEW FILTERS)
# =========================================================
class StaffDashboardView(APIView):
    permission_classes = [IsAuthenticated, IsStaff]

    def get(self, request):
        user = request.user
        try:
             active_year = AcademicYear.objects.get(is_current=True)
        except:
             return Response({"error": "No Active Year"}, 500)

        if not hasattr(user, 'staff_profile'):
            return Response({"error": "Access Denied"}, 403)
        
        my_role = user.staff_profile.role 
        
        date_param = request.query_params.get('date')
        source = request.query_params.get('source') # common, all_staff, my_role

        data = {}

        # 1. Common Announcements
        if not source or source == 'common':
            qs = CommonAnnouncement.objects.filter(academic_year=active_year).order_by('-date')
            if date_param: qs = qs.filter(date=date_param)
            data['common_announcements'] = CommonAnnouncementSerializer(qs, many=True).data

        # 2. All Staff Announcements
        if not source or source == 'all_staff':
            qs = StaffAnnouncement.objects.filter(academic_year=active_year, visibility='ALL_STAFF').order_by('-date')
            if date_param: qs = qs.filter(date=date_param)
            data['all_staff_announcements'] = StaffAnnouncementSerializer(qs, many=True).data

        # 3. My Role Announcements
        if not source or source == 'my_role':
            qs = StaffAnnouncement.objects.filter(
                academic_year=active_year, 
                visibility='ROLE_SPECIFIC',
                target_role__iexact=my_role
            ).order_by('-date')
            if date_param: qs = qs.filter(date=date_param)
            data['role_specific_announcements'] = StaffAnnouncementSerializer(qs, many=True).data
        
        return Response({
            "status": 200,
            "viewer_role": my_role,
            "year": active_year.name,
            "viewing_date": date_param if date_param else "All Time",
            "filter": source if source else "All",
            **data
        })


# =========================================================
#  7. ADMIN COMMON ANNOUNCEMENTS (Added PUT/DELETE)
# =========================================================
class AdminCommonAnnouncementView(APIView):
    permission_classes = [IsAuthenticated, IsAdmin]

    def get(self, request):
        announcements = CommonAnnouncement.objects.all().order_by('-date')
        
        # 1. Filter by Year
        year_filter = request.query_params.get('year')
        if year_filter:
            announcements = announcements.filter(academic_year__name=year_filter)

        # 2. Filter by Date (THIS WAS MISSING)
        date_param = request.query_params.get('date')
        if date_param:
            announcements = announcements.filter(date=date_param)

        serializer = CommonAnnouncementSerializer(announcements, many=True)
        return Response({"status": 200, "data": serializer.data})

    def post(self, request):
        try:
            active_year = AcademicYear.objects.get(is_current=True)
        except:
             return Response({"error": "No Active Year"}, 500)

        serializer = CommonAnnouncementSerializer(data=request.data)
        if serializer.is_valid():
            announcement = serializer.save(academic_year=active_year)
            try:
                send_notification_to_users(
                    users=_active_school_users(active_year.school),
                    sender=request.user,
                    title=f"School Update: {announcement.title}",
                    message=_announcement_preview(announcement.description),
                    notif_type="Update",
                    data={
                        "screen": "notifications",
                        "permission": "updates",
                    },
                )
            except Exception as e:
                print(f"Notification Error: {e}")
            return Response({"status": 201, "message": "Common Announcement Posted", "data": serializer.data})
        return Response(serializer.errors, status=400)
        

class AdminCommonAnnouncementDetailView(APIView):
    permission_classes = [IsAuthenticated, IsAdmin]

    def get_object(self, pk):
        return get_object_or_404(CommonAnnouncement, pk=pk)

    def put(self, request, pk):
        announcement = self.get_object(pk)
        serializer = CommonAnnouncementSerializer(announcement, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save()
            return Response({"status": 200, "message": "Updated", "data": serializer.data})
        return Response(serializer.errors, status=400)

    def delete(self, request, pk):
        announcement = self.get_object(pk)
        announcement.delete()
        return Response({"status": 200, "message": "Deleted"})


class AdminCommonAnnouncementPaginatedView(APIView):
    permission_classes = [IsAuthenticated, IsAdmin]

    def get(self, request):
        queryset = CommonAnnouncement.objects.all()

        year_filter = request.query_params.get('year')
        if year_filter:
            queryset = queryset.filter(academic_year__name=year_filter)

        date_param = request.query_params.get('date')
        if date_param:
            queryset = queryset.filter(date=date_param)

        search_text = (request.query_params.get('q') or '').strip()
        if search_text:
            queryset = queryset.filter(
                Q(title__icontains=search_text)
                | Q(description__icontains=search_text)
            )

        sort_by = (request.query_params.get('sort_by') or 'date').strip().lower()
        sort_dir = (request.query_params.get('sort_dir') or 'desc').strip().lower()
        sort_map = {
            'date': 'date',
            'title': 'title',
            'created_at': 'created_at',
        }
        order_field = sort_map.get(sort_by, 'date')
        if sort_dir != 'asc':
            order_field = f'-{order_field}'
        queryset = queryset.order_by(order_field, '-id')

        page, page_size, error_response = _parse_pagination(request.query_params)
        if error_response:
            return error_response

        total = queryset.count()
        page, start, end, pagination = _build_pagination_payload(page, page_size, total)
        serializer = CommonAnnouncementSerializer(queryset[start:end], many=True)
        return Response({
            "status": 200,
            "count": total,
            "data": serializer.data,
            "pagination": pagination,
        })


# =========================================================
#  8. ADMIN: TEACHER ANNOUNCEMENTS (NEW)
# =========================================================
class AdminTeacherAnnouncementView(APIView):
    permission_classes = [IsAuthenticated, IsAdmin]

    def get(self, request):
        queryset = TeacherAnnouncement.objects.all().order_by('-date')
        
        # Filters
        date_param = request.query_params.get('date')
        visibility = request.query_params.get('visibility') # ALL_TEACHERS or ROLE_SPECIFIC
        target_role = request.query_params.get('role') # CLASS_TEACHER or SUBJECT_TEACHER

        if date_param: queryset = queryset.filter(date=date_param)
        if visibility: queryset = queryset.filter(visibility=visibility)
        if target_role: queryset = queryset.filter(target_role__iexact=target_role)

        serializer = TeacherAnnouncementSerializer(queryset, many=True)
        return Response({"status": 200, "data": serializer.data})


class AdminTeacherAnnouncementPaginatedView(APIView):
    permission_classes = [IsAuthenticated, IsAdmin]

    def get(self, request):
        queryset = TeacherAnnouncement.objects.all()

        date_param = request.query_params.get('date')
        visibility = request.query_params.get('visibility')
        target_role = request.query_params.get('role')

        if date_param:
            queryset = queryset.filter(date=date_param)
        if visibility:
            queryset = queryset.filter(visibility=visibility)
        if target_role:
            queryset = queryset.filter(target_role__iexact=target_role)

        search_text = (request.query_params.get('q') or '').strip()
        if search_text:
            queryset = queryset.filter(
                Q(title__icontains=search_text)
                | Q(description__icontains=search_text)
                | Q(target_role__icontains=search_text)
            )

        sort_by = (request.query_params.get('sort_by') or 'date').strip().lower()
        sort_dir = (request.query_params.get('sort_dir') or 'desc').strip().lower()
        sort_map = {
            'date': 'date',
            'title': 'title',
            'created_at': 'created_at',
        }
        order_field = sort_map.get(sort_by, 'date')
        if sort_dir != 'asc':
            order_field = f'-{order_field}'
        queryset = queryset.order_by(order_field, '-id')

        page, page_size, error_response = _parse_pagination(request.query_params)
        if error_response:
            return error_response

        total = queryset.count()
        page, start, end, pagination = _build_pagination_payload(page, page_size, total)
        serializer = TeacherAnnouncementSerializer(queryset[start:end], many=True)
        return Response({
            "status": 200,
            "count": total,
            "data": serializer.data,
            "pagination": pagination,
        })

    def post(self, request):
        try:
            active_year = AcademicYear.objects.get(is_current=True)
        except:
             return Response({"error": "No Active Year"}, 500)

        serializer = TeacherAnnouncementSerializer(data=request.data)
        if serializer.is_valid():
            announcement = serializer.save(academic_year=active_year)
            try:
                recipients = _active_school_users(active_year.school, ['teacher'])
                if announcement.visibility == 'ROLE_SPECIFIC':
                    if announcement.target_role == 'CLASS_TEACHER':
                        recipients = recipients.filter(
                            teacher_profile__class_teacher_of__academic_year=active_year
                        )
                    elif announcement.target_role == 'SUBJECT_TEACHER':
                        recipients = recipients.filter(
                            teacher_profile__allocations__academic_year=active_year
                        )
                send_notification_to_users(
                    users=recipients.distinct(),
                    sender=request.user,
                    title=f"Teacher Update: {announcement.title}",
                    message=_announcement_preview(announcement.description),
                    notif_type="Teacher Update",
                    data={
                        "screen": "teacher_announcements",
                        "permission": "announcements",
                    },
                )
            except Exception as e:
                print(f"Notification Error: {e}")
            return Response({"status": 201, "message": "Created", "data": serializer.data})
        return Response(serializer.errors, status=400)

class AdminTeacherAnnouncementDetailView(APIView):
    permission_classes = [IsAuthenticated, IsAdmin]

    def get_object(self, pk):
        return get_object_or_404(TeacherAnnouncement, pk=pk)

    def put(self, request, pk):
        obj = self.get_object(pk)
        serializer = TeacherAnnouncementSerializer(obj, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save()
            return Response({"status": 200, "message": "Updated", "data": serializer.data})
        return Response(serializer.errors, status=400)

    def delete(self, request, pk):
        obj = self.get_object(pk)
        obj.delete()
        return Response({"status": 200, "message": "Deleted"})


# =========================================================
#  9. TEACHER: VIEW ADMIN ANNOUNCEMENTS (NEW DASHBOARD)
# =========================================================
class TeacherAdminAnnouncementDashboardView(APIView):
    permission_classes = [IsAuthenticated, IsTeacher]

    def get(self, request):
        user = request.user
        teacher_profile = user.teacher_profile
        
        try:
             active_year = AcademicYear.objects.get(is_current=True)
        except:
             return Response({"error": "No Active Year"}, 500)

        # Filters
        date_param = request.query_params.get('date')
        filter_type = request.query_params.get('filter') # common, all_teachers, class_teacher, subject_teacher

        data = {}

        # 1. Common (School Wide)
        if not filter_type or filter_type == 'common':
            qs = CommonAnnouncement.objects.filter(academic_year=active_year).order_by('-date')
            if date_param: qs = qs.filter(date=date_param)
            data['common'] = CommonAnnouncementSerializer(qs, many=True).data

        # 2. All Teachers
        if not filter_type or filter_type == 'all_teachers':
            qs = TeacherAnnouncement.objects.filter(
                academic_year=active_year, 
                visibility='ALL_TEACHERS'
            ).order_by('-date')
            if date_param: qs = qs.filter(date=date_param)
            data['all_teachers'] = TeacherAnnouncementSerializer(qs, many=True).data

        # 3. Class Teacher (Check if user IS a class teacher first)
        if not filter_type or filter_type == 'class_teacher':
            is_class_teacher = ClassTeacher.objects.filter(teacher=teacher_profile, academic_year=active_year).exists()
            
            if is_class_teacher:
                qs = TeacherAnnouncement.objects.filter(
                    academic_year=active_year,
                    visibility='ROLE_SPECIFIC',
                    target_role='CLASS_TEACHER'
                ).order_by('-date')
                if date_param: qs = qs.filter(date=date_param)
                data['class_teacher_updates'] = TeacherAnnouncementSerializer(qs, many=True).data
            else:
                data['class_teacher_updates'] = [] # Empty if not a class teacher

        # 4. Subject Teacher (Check if user IS allocated subjects)
        if not filter_type or filter_type == 'subject_teacher':
            is_subject_teacher = TeacherAllocation.objects.filter(teacher=teacher_profile, academic_year=active_year).exists()
            
            if is_subject_teacher:
                qs = TeacherAnnouncement.objects.filter(
                    academic_year=active_year,
                    visibility='ROLE_SPECIFIC',
                    target_role='SUBJECT_TEACHER'
                ).order_by('-date')
                if date_param: qs = qs.filter(date=date_param)
                data['subject_teacher_updates'] = TeacherAnnouncementSerializer(qs, many=True).data
            else:
                data['subject_teacher_updates'] = []

        return Response({
            "status": 200,
            "year": active_year.name,
            "viewing_date": date_param if date_param else "All Time",
            "filter_applied": filter_type if filter_type else "All",
            **data
        })


class AnnouncementTargetTypesView(APIView):
    """
    Returns a single combined list of 8 target groups:
    6 Staff Types + 2 Teacher Types.
    """
    permission_classes = [IsAuthenticated, IsAdmin]

    def get(self, request):
        # We define and combine them directly into one list
        target_types = [
            {'id': 'admin_staff', 'label': 'Admin Staff'},
            {'id': 'finance_staff', 'label': 'Finance Staff'},
            {'id': 'it_staff', 'label': 'IT Staff'},
            {'id': 'operations_staff', 'label': 'Operations Staff'},
            {'id': 'transport_staff', 'label': 'Transport Staff'},
            {'id': 'external_staff', 'label': 'External Staff'},
            {'id': 'class_teacher', 'label': 'Class Teacher'},
            {'id': 'subject_teacher', 'label': 'Subject Teacher'},
        ]

        return Response({
            "status": 200,
            "data": target_types
        })
    


class AdminAnnouncementsOverviewView(APIView):
    """
    Admin Dashboard API that provides:
    1. Overview statistics
    2. Recent announcements (last 7 days)
    3. Past announcements (older than 7 days)
    4. Filter by announcement type (teacher/staff/common)
    """
    permission_classes = [IsAuthenticated, IsAdmin]

    def get(self, request):
        try:
            # Get current academic year
            active_year = AcademicYear.objects.get(is_current=True)
        except AcademicYear.DoesNotExist:
            return Response({
                "status": 404,
                "error": "No Active Academic Year found.",
                "message": "Please configure an active academic year in the system."
            }, status=404)
        except Exception as e:
            return Response({
                "status": 500,
                "error": "System Error",
                "message": "Unable to fetch academic year information."
            }, status=500)

        # Get query parameters with validation
        announcement_type = request.query_params.get('type', 'all').lower()
        
        # Validate announcement type
        valid_types = ['teacher', 'staff', 'common', 'all']
        if announcement_type not in valid_types:
            return Response({
                "status": 400,
                "error": "Invalid announcement type",
                "message": f"Type must be one of: {', '.join(valid_types)}",
                "valid_types": valid_types
            }, status=400)
        
        # Validate days parameter
        try:
            days_limit = int(request.query_params.get('days', 7))
            if days_limit <= 0:
                raise ValueError("Days must be positive")
        except ValueError:
            return Response({
                "status": 400,
                "error": "Invalid days parameter",
                "message": "Days must be a positive integer"
            }, status=400)
        
        # Calculate date thresholds
        today = date.today()
        recent_threshold = today - timedelta(days=days_limit)
        
        # Initialize response structure with default empty values
        response_data = {
            "status": 200,
            "academic_year": active_year.name,
            "filters_applied": {
                "type": announcement_type,
                "recent_days": days_limit
            },
            "overview": {
                "total_announcements": 0,
                "by_type": {},
                "recent_count": 0,
                "past_count": 0,
                "has_data": False
            },
            "recent_announcements": {
                "teacher": [],
                "staff": [],
                "common": []
            },
            "past_announcements": {
                "teacher": [],
                "staff": [],
                "common": []
            },
            "messages": []
        }

        try:
            # ==========================================
            # 1. TEACHER ANNOUNCEMENTS
            # ==========================================
            if announcement_type in ['teacher', 'all']:
                teacher_announcements = TeacherAnnouncement.objects.filter(
                    academic_year=active_year
                )
                
                teacher_total = teacher_announcements.count()
                
                if teacher_total > 0:
                    teacher_recent = teacher_announcements.filter(date__gte=recent_threshold).count()
                    teacher_past = teacher_total - teacher_recent
                    
                    # By visibility
                    teacher_by_visibility = list(teacher_announcements.values('visibility').annotate(count=Count('id')))
                    
                    # Update overview
                    response_data["overview"]["by_type"]["teacher"] = {
                        "total": teacher_total,
                        "recent": teacher_recent,
                        "past": teacher_past,
                        "visibility_distribution": teacher_by_visibility,
                        "has_data": True
                    }
                    
                    # Get recent teacher announcements
                    recent_teacher = teacher_announcements.filter(date__gte=recent_threshold).order_by('-date')[:10]
                    if recent_teacher.exists():
                        response_data["recent_announcements"]["teacher"] = TeacherAnnouncementSerializer(recent_teacher, many=True).data
                    
                    # Get past teacher announcements
                    past_teacher = teacher_announcements.filter(date__lt=recent_threshold).order_by('-date')[:20]
                    if past_teacher.exists():
                        response_data["past_announcements"]["teacher"] = TeacherAnnouncementSerializer(past_teacher, many=True).data
                else:
                    response_data["overview"]["by_type"]["teacher"] = {
                        "total": 0,
                        "recent": 0,
                        "past": 0,
                        "visibility_distribution": [],
                        "has_data": False
                    }
                    response_data["messages"].append("No teacher announcements found for the current academic year.")

            # ==========================================
            # 2. STAFF ANNOUNCEMENTS
            # ==========================================
            if announcement_type in ['staff', 'all']:
                staff_announcements = StaffAnnouncement.objects.filter(
                    academic_year=active_year
                )
                
                staff_total = staff_announcements.count()
                
                if staff_total > 0:
                    staff_recent = staff_announcements.filter(date__gte=recent_threshold).count()
                    staff_past = staff_total - staff_recent
                    
                    # By visibility
                    staff_by_visibility = list(staff_announcements.values('visibility').annotate(count=Count('id')))
                    
                    # By target role
                    staff_by_role = list(staff_announcements.filter(
                        visibility='ROLE_SPECIFIC'
                    ).values('target_role').annotate(count=Count('id')))
                    
                    # Update overview
                    response_data["overview"]["by_type"]["staff"] = {
                        "total": staff_total,
                        "recent": staff_recent,
                        "past": staff_past,
                        "visibility_distribution": staff_by_visibility,
                        "role_distribution": staff_by_role,
                        "has_data": True
                    }
                    
                    # Get recent staff announcements
                    recent_staff = staff_announcements.filter(date__gte=recent_threshold).order_by('-date')[:10]
                    if recent_staff.exists():
                        response_data["recent_announcements"]["staff"] = StaffAnnouncementSerializer(recent_staff, many=True).data
                    
                    # Get past staff announcements
                    past_staff = staff_announcements.filter(date__lt=recent_threshold).order_by('-date')[:20]
                    if past_staff.exists():
                        response_data["past_announcements"]["staff"] = StaffAnnouncementSerializer(past_staff, many=True).data
                else:
                    response_data["overview"]["by_type"]["staff"] = {
                        "total": 0,
                        "recent": 0,
                        "past": 0,
                        "visibility_distribution": [],
                        "role_distribution": [],
                        "has_data": False
                    }
                    response_data["messages"].append("No staff announcements found for the current academic year.")

            # ==========================================
            # 3. COMMON ANNOUNCEMENTS
            # ==========================================
            if announcement_type in ['common', 'all']:
                common_announcements = CommonAnnouncement.objects.filter(
                    academic_year=active_year
                )
                
                common_total = common_announcements.count()
                
                if common_total > 0:
                    common_recent = common_announcements.filter(date__gte=recent_threshold).count()
                    common_past = common_total - common_recent
                    
                    # Update overview
                    response_data["overview"]["by_type"]["common"] = {
                        "total": common_total,
                        "recent": common_recent,
                        "past": common_past,
                        "has_data": True
                    }
                    
                    # Get recent common announcements
                    recent_common = common_announcements.filter(date__gte=recent_threshold).order_by('-date')[:10]
                    if recent_common.exists():
                        response_data["recent_announcements"]["common"] = CommonAnnouncementSerializer(recent_common, many=True).data
                    
                    # Get past common announcements
                    past_common = common_announcements.filter(date__lt=recent_threshold).order_by('-date')[:20]
                    if past_common.exists():
                        response_data["past_announcements"]["common"] = CommonAnnouncementSerializer(past_common, many=True).data
                else:
                    response_data["overview"]["by_type"]["common"] = {
                        "total": 0,
                        "recent": 0,
                        "past": 0,
                        "has_data": False
                    }
                    response_data["messages"].append("No common announcements found for the current academic year.")

            # ==========================================
            # 4. CALCULATE TOTALS AND CHECK FOR DATA
            # ==========================================
            if announcement_type == 'all':
                # Calculate totals
                teacher_stats = response_data["overview"]["by_type"].get("teacher", {})
                staff_stats = response_data["overview"]["by_type"].get("staff", {})
                common_stats = response_data["overview"]["by_type"].get("common", {})
                
                total_announcements = (
                    teacher_stats.get("total", 0) +
                    staff_stats.get("total", 0) +
                    common_stats.get("total", 0)
                )
                
                total_recent = (
                    teacher_stats.get("recent", 0) +
                    staff_stats.get("recent", 0) +
                    common_stats.get("recent", 0)
                )
                
                total_past = (
                    teacher_stats.get("past", 0) +
                    staff_stats.get("past", 0) +
                    common_stats.get("past", 0)
                )
                
                response_data["overview"]["total_announcements"] = total_announcements
                response_data["overview"]["recent_count"] = total_recent
                response_data["overview"]["past_count"] = total_past
                response_data["overview"]["has_data"] = total_announcements > 0
                
                # Activity timeline (last 30 days) - only if we have data
                if total_announcements > 0:
                    timeline_data = self.get_activity_timeline(active_year)
                    response_data["overview"]["activity_timeline"] = timeline_data
                    
                    # Check if timeline has data
                    timeline_has_data = any(day["total"] > 0 for day in timeline_data)
                    response_data["overview"]["timeline_has_data"] = timeline_has_data
                    
                    if not timeline_has_data:
                        response_data["messages"].append("No announcement activity in the last 30 days.")
                else:
                    response_data["overview"]["activity_timeline"] = []
                    response_data["overview"]["timeline_has_data"] = False
            else:
                # For single type, check if that type has data
                type_stats = response_data["overview"]["by_type"].get(announcement_type, {})
                type_total = type_stats.get("total", 0)
                response_data["overview"]["has_data"] = type_total > 0
                
                if type_total == 0 and not response_data["messages"]:
                    response_data["messages"].append(f"No {announcement_type} announcements found for the current academic year.")

            # ==========================================
            # 5. ADDITIONAL CHECKS FOR EMPTY DATA
            # ==========================================
            
            # Check if all recent announcements are empty
            recent_empty = (
                len(response_data["recent_announcements"]["teacher"]) == 0 and
                len(response_data["recent_announcements"]["staff"]) == 0 and
                len(response_data["recent_announcements"]["common"]) == 0
            )
            
            # Check if all past announcements are empty
            past_empty = (
                len(response_data["past_announcements"]["teacher"]) == 0 and
                len(response_data["past_announcements"]["staff"]) == 0 and
                len(response_data["past_announcements"]["common"]) == 0
            )
            
            if recent_empty and days_limit > 0:
                response_data["messages"].append(f"No announcements found in the last {days_limit} days.")
            
            if past_empty and not recent_empty and days_limit > 0:
                response_data["messages"].append("No announcements found before the recent period.")
            
            # Add success message if we have data
            if response_data["overview"]["has_data"]:
                response_data["messages"].insert(0, "Data retrieved successfully.")
                
        except Exception as e:
            return Response({
                "status": 500,
                "error": "Processing Error",
                "message": "An error occurred while processing announcement data.",
                "details": str(e) if settings.DEBUG else None
            }, status=500)

        return Response(response_data)

    def get_activity_timeline(self, academic_year):
        """
        Get announcement activity for the last 30 days
        Returns empty list if no data or error occurs
        """
        try:
            thirty_days_ago = date.today() - timedelta(days=30)
            timeline_data = []
            
            # Teacher announcements timeline
            teacher_timeline = TeacherAnnouncement.objects.filter(
                academic_year=academic_year,
                date__gte=thirty_days_ago
            ).values('date').annotate(
                teacher_count=Count('id')
            ).order_by('date')
            
            # Staff announcements timeline  
            staff_timeline = StaffAnnouncement.objects.filter(
                academic_year=academic_year,
                date__gte=thirty_days_ago
            ).values('date').annotate(
                staff_count=Count('id')
            ).order_by('date')
            
            # Common announcements timeline
            common_timeline = CommonAnnouncement.objects.filter(
                academic_year=academic_year,
                date__gte=thirty_days_ago
            ).values('date').annotate(
                common_count=Count('id')
            ).order_by('date')
            
            # If no data at all, return empty list
            if not teacher_timeline.exists() and not staff_timeline.exists() and not common_timeline.exists():
                return []
            
            # Combine all timelines into a dictionary by date
            timeline_dict = defaultdict(lambda: {
                'date': None,
                'teacher': 0,
                'staff': 0,
                'common': 0,
                'total': 0
            })
            
            # Process teacher timeline
            for entry in teacher_timeline:
                entry_date = entry['date']
                timeline_dict[entry_date]['date'] = entry_date
                timeline_dict[entry_date]['teacher'] = entry['teacher_count']
                timeline_dict[entry_date]['total'] += entry['teacher_count']
            
            # Process staff timeline
            for entry in staff_timeline:
                entry_date = entry['date']
                timeline_dict[entry_date]['date'] = entry_date
                timeline_dict[entry_date]['staff'] = entry['staff_count']
                timeline_dict[entry_date]['total'] += entry['staff_count']
            
            # Process common timeline
            for entry in common_timeline:
                entry_date = entry['date']
                timeline_dict[entry_date]['date'] = entry_date
                timeline_dict[entry_date]['common'] = entry['common_count']
                timeline_dict[entry_date]['total'] += entry['common_count']
            
            # Convert to list and sort by date
            for date_key in sorted(timeline_dict.keys()):
                timeline_dict[date_key]['date'] = date_key.strftime('%Y-%m-%d')
                timeline_data.append(timeline_dict[date_key])
            
            return timeline_data
            
        except Exception as e:
            return []  # Return empty list on error
