from django.urls import path
from .views import (
    # 1. Existing Views
    CSVUploadView, 
    AdminProfileView,
    
    # 2. Student Management Views
    AdminStudentListCreateView, 
    AdminStudentPaginatedListView,
    AdminStudentDetailView,
    
    # 3. Teacher Management Views
    AdminTeacherListCreateView, 
    AdminTeacherPaginatedListView,
    AdminTeacherDetailView,
    
    # 4. Staff Management Views
    AdminStaffListCreateView, 
    AdminStaffPaginatedListView,
    AdminStaffDetailView,

    AssignClassTeacherView, # <--- Add this here!
    AdminDashboardView,

    TeachersByClassView,

    ClassTeacherManagerView,
    SubjectTeacherManagerView,
    AdminDashboardStatsView,
    TransportDashboardStatsView,
    BulkProfileImageZipUploadView,

    RecentActivitiesView, #siva bro code 
    AdminStaffOverviewView,
    AdminTeacherOverviewView,

    DashboardStatsView,

    AdminRecentActivitiesView,

    AdminInventoryUpdatesView,

    AdminActivitySummaryView,

    AdminInventoryChartView,

    AdminStaffTodayWorkStatusView,
    AdminSidebarCountsView,
    SuperAdminAdminUserListCreateView,

    
)

urlpatterns = [
    # --- EXISTING: BULK UPLOAD ---
    path("csv/", CSVUploadView.as_view(), name="csv-upload"),

    # --- ADMIN'S OWN PROFILE ---
    path("profile/", AdminProfileView.as_view(), name="admin-profile"),
    path("admin-users/", SuperAdminAdminUserListCreateView.as_view(), name="super-admin-admin-users"),

    path('dashboard/', AdminDashboardView.as_view(), name='admin-dashboard'), # <--- NEW

    # --- MANAGE STUDENTS ---
    # GET (List all) / POST (Add one manually)
    path("students/", AdminStudentListCreateView.as_view(), name="admin-student-list"),
    path("students/paginated/", AdminStudentPaginatedListView.as_view(), name="admin-student-list-paginated"),


    # GET (View one) / PUT (Edit) / DELETE (Remove)
    path("students/<str:student_id>/", AdminStudentDetailView.as_view(), name="admin-student-detail"),

    # --- MANAGE TEACHERS ---
    path("teachers/", AdminTeacherListCreateView.as_view(), name="admin-teacher-list"),
    path("teachers/paginated/", AdminTeacherPaginatedListView.as_view(), name="admin-teacher-list-paginated"),
    path("teachers/<str:teacher_id>/", AdminTeacherDetailView.as_view(), name="admin-teacher-detail"),

    # --- MANAGE STAFF ---
    path("staff/", AdminStaffListCreateView.as_view(), name="admin-staff-list"),
    path("staff/paginated/", AdminStaffPaginatedListView.as_view(), name="admin-staff-list-paginated"),

    # -----------------------------------------------------------------

    # siva bro's url's

    path('staff/overview/', AdminStaffOverviewView.as_view(), name='admin-staff-overview'),

    # --------------------------------------------------------

    path("staff/<str:staff_id>/", AdminStaffDetailView.as_view(), name="admin-staff-detail"),

    path('assign-teacher/', AssignClassTeacherView.as_view(), name='assign-teacher'),

    path('teachers-by-class/', TeachersByClassView.as_view(), name='teachers-by-class'),

    # 2. MANAGE: Class Teachers (GET / DELETE)
    path('manage/class-teachers/', ClassTeacherManagerView.as_view()),

    # 3. MANAGE: Subject Teachers (DELETE)
    path('manage/subject-teachers/', SubjectTeacherManagerView.as_view()),

    # ... your other urls
    path('dashboard/stats/', AdminDashboardStatsView.as_view(), name='admin-dashboard-stats'),
    
    # Transport Dashboard
    path('dashboard/transport/', TransportDashboardStatsView.as_view(), name='transport-dashboard'),

    # ... your other admin urls ...
    path('bulk-upload-images/', BulkProfileImageZipUploadView.as_view(), name='bulk-upload-images'),

    # ----- siva -----

    path('teacher/overview/', AdminTeacherOverviewView.as_view(), name='admin-teacher-overview'),

    path('dashboard-stats/', DashboardStatsView.as_view(), name='dashboard-stats'),

    path('recent-activities/', AdminRecentActivitiesView.as_view(), name='admin-recent-activities'),
    path('activity-summary/', AdminActivitySummaryView.as_view(), name='admin-activity-summary'),
    path('inventory-updates/', AdminInventoryUpdatesView.as_view(), name='admin-inventory-updates'),
    path('inventory-chart/', AdminInventoryChartView.as_view(), name='admin-inventory-chart'),
    path('staff-work-today/', AdminStaffTodayWorkStatusView.as_view(), name='admin-staff-work-today'),
    path('sidebar-counts/', AdminSidebarCountsView.as_view(), name='admin-sidebar-counts'),

    
    ]
