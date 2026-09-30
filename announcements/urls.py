from django.urls import path
from .views import (
    CreateAnnouncementView, 
    NoticeBoardView, 
    MyPostView, 
    StudentAnnouncementView,
    StudentAnnouncementWithDateView,
    # --- STAFF IMPORTS ---
    AdminStaffAnnouncementView,
    AdminStaffAnnouncementPaginatedView,
    AdminStaffAnnouncementDetailView,
    StaffDashboardView,
    # --- COMMON IMPORTS ---
    AdminCommonAnnouncementView,
    AdminCommonAnnouncementPaginatedView,
    AdminCommonAnnouncementDetailView, # <--- Added this
    # --- TEACHER ADMIN IMPORTS ---
    AdminTeacherAnnouncementView,      # <--- Added this
    AdminTeacherAnnouncementPaginatedView,
    AdminTeacherAnnouncementDetailView,# <--- Added this
    TeacherAdminAnnouncementDashboardView, # <--- Added this
    AnnouncementTargetTypesView, # <--- Added this
    AdminAnnouncementsOverviewView
)

urlpatterns = [
    # ==========================================
    # 1. TEACHER -> STUDENT (Existing)
    # ==========================================
    path('create/', CreateAnnouncementView.as_view()),
    path('notice-board/', NoticeBoardView.as_view()), 
    path('my-posts/', MyPostView.as_view()),         
    path('student/board/', StudentAnnouncementView.as_view()),
    path('student/board-with-date/', StudentAnnouncementWithDateView.as_view()),

    # ==========================================
    # 2. ADMIN -> COMMON (School Events)
    # ==========================================
    path('admin/common-announcements/', AdminCommonAnnouncementView.as_view(), name='admin-common-list'),
    path('admin/common-announcements/paginated/', AdminCommonAnnouncementPaginatedView.as_view(), name='admin-common-list-paginated'),
    path('admin/common-announcements/<int:pk>/', AdminCommonAnnouncementDetailView.as_view(), name='admin-common-detail'), # <--- Added

    # ==========================================
    # 3. ADMIN -> STAFF (Non-Teaching)
    # ==========================================
    path('admin/staff-announcements/', AdminStaffAnnouncementView.as_view(), name='admin-staff-list'),
    path('admin/staff-announcements/paginated/', AdminStaffAnnouncementPaginatedView.as_view(), name='admin-staff-list-paginated'),
    path('admin/staff-announcements/<int:pk>/', AdminStaffAnnouncementDetailView.as_view(), name='admin-staff-detail'),
    
    # STAFF DASHBOARD
    path('staff/dashboard/', StaffDashboardView.as_view(), name='staff-dashboard'),

    # ==========================================
    # 4. ADMIN -> TEACHERS (New Feature)
    # ==========================================
    # Admin creates/views list
    path('admin/teacher-announcements/', AdminTeacherAnnouncementView.as_view(), name='admin-teacher-list'),
    path('admin/teacher-announcements/paginated/', AdminTeacherAnnouncementPaginatedView.as_view(), name='admin-teacher-list-paginated'),
    
    # Admin edits/deletes specific post
    path('admin/teacher-announcements/<int:pk>/', AdminTeacherAnnouncementDetailView.as_view(), name='admin-teacher-detail'),

    # TEACHER DASHBOARD (To view Admin posts)
    path('teacher/admin-board/', TeacherAdminAnnouncementDashboardView.as_view(), name='teacher-admin-board'),

    path('target-types/', AnnouncementTargetTypesView.as_view(), name='announcement-targets'),

    path('admin/dashboard/overview/', AdminAnnouncementsOverviewView.as_view(), name='admin-dashboard-overview'),
]
