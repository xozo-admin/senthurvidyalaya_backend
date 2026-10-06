from django.contrib import admin
from django.urls import path, include, re_path
from django.conf import settings             # <--- Import settings
from django.conf.urls.static import static   # <--- Import static
from django.views.static import serve
import re

urlpatterns = [
    # 1. Django Admin Panel (Standard Web Interface)
    path('admin/', admin.site.urls),

    # 3. 👇 ADD THIS LINE for School Admin 👇
    path('api/schooladmin/', include('schooladmin.urls')),
    
    # 2. Authentication (Login/Register/Logout)
    path("api/accounts/", include("accounts.urls")),
    
    # 3. School Admin Dashboard (CSV Upload + Manage Users)
    path("api/setup/", include("schooladmin.urls")),
    
    # 4. Student App (My Profile)
    path("api/student/", include("students.urls")),
    
    # 5. Teacher App (My Profile)
    path("api/teacher/", include("teachers.urls")),
    
    # 6. Staff App (My Profile)
    path("api/staff/", include("staff.urls")),

    path("api/academics/", include("academics.urls")),

    path("api/attendance/", include("attendance.urls")),

    path('api/timetable/', include('timetable.urls')),

    path('api/subjects/', include('subjects.urls')),

    path("api/exams/", include("exams.urls")),

    path('api/fees/', include('fees.urls')),

    # 7. Assignments App (NEW)
    path('api/assignments/', include('assignments.urls')),

    path('api/reports/', include('reports.urls')),

    path('api/tasks/', include('tasks.urls')),

    path('api/announcements/', include('announcements.urls')),

    path('api/performance/', include('performance.urls')),

    path('api/class-resources/', include('class_resources.urls')),

    path('api/subject-materials/', include('subject_materials.urls')),

    # --- ADD THIS LINE FOR TRANSPORT ---
    path('api/transport/', include('transport.urls')),

    path('api/staff-work/', include('staff_work.urls')),

    path('api/inventory/', include('inventory.urls')),

    path('api/leaves/', include('leave_management.urls')),

    # 1. Holidays (Global Calendar)
    path('api/holidays/', include('holidays.urls')),

    # 2. Salary (Payroll Logic)
    path('api/salary/', include('salary.urls')),

    path('api/school/', include('school.urls')),

    path('api/notifications/', include('notifications.urls')),
    path('api/meetings/', include('meeting.urls')),
    path('api/hostel/', include('hostel.urls')),

    # --- siva ---

    path('api/audit/', include('audit.urls')),
    path('api/promotions/', include('promotions.urls')),
]

# Keep development media behavior. In production, expose only the school and
# institution logos needed by public-facing dashboard headers; other uploaded
# files can include private documents and must not be served anonymously.
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
else:
    media_prefix = re.escape(settings.MEDIA_URL.lstrip('/'))
    urlpatterns += [
        re_path(
            rf'^{media_prefix}(?P<path>(?:schools|institutions)/logos/.*)$',
            serve,
            {'document_root': settings.MEDIA_ROOT},
        ),
    ]
