from django.urls import path

from .views import (
    AdminHostelAllocationView,
    AdminHostelAttendanceView,
    AdminHostelBedDetailView,
    AdminHostelBedView,
    AdminHostelBlockDetailView,
    AdminHostelBlockView,
    AdminHostelDashboardView,
    AdminHostelIncidentView,
    AdminHostelInOutView,
    AdminHostelRoomDetailView,
    AdminHostelRoomView,
    AdminHostelWardenAssignmentView,
    StaffHostelAttendanceView,
    StaffHostelIncidentView,
    StaffHostelInOutView,
    StaffHostelOccupancyView,
    StaffMyHostelDashboardView,
    StudentMyHostelView,
)

urlpatterns = [
    # Admin
    path('admin/dashboard/', AdminHostelDashboardView.as_view(), name='hostel-admin-dashboard'),
    path('admin/blocks/', AdminHostelBlockView.as_view(), name='hostel-admin-blocks'),
    path('admin/blocks/<int:pk>/', AdminHostelBlockDetailView.as_view(), name='hostel-admin-block-detail'),
    path('admin/rooms/', AdminHostelRoomView.as_view(), name='hostel-admin-rooms'),
    path('admin/rooms/<int:pk>/', AdminHostelRoomDetailView.as_view(), name='hostel-admin-room-detail'),
    path('admin/beds/', AdminHostelBedView.as_view(), name='hostel-admin-beds'),
    path('admin/beds/<int:pk>/', AdminHostelBedDetailView.as_view(), name='hostel-admin-bed-detail'),
    path('admin/warden-assignments/', AdminHostelWardenAssignmentView.as_view(), name='hostel-admin-warden-assignments'),
    path('admin/allocations/', AdminHostelAllocationView.as_view(), name='hostel-admin-allocations'),
    path('admin/attendance/', AdminHostelAttendanceView.as_view(), name='hostel-admin-attendance'),
    path('admin/incidents/', AdminHostelIncidentView.as_view(), name='hostel-admin-incidents'),
    path('admin/in-out/', AdminHostelInOutView.as_view(), name='hostel-admin-in-out'),

    # Hostel Staff (Wardens)
    path('staff/dashboard/', StaffMyHostelDashboardView.as_view(), name='hostel-staff-dashboard'),
    path('staff/occupancy/', StaffHostelOccupancyView.as_view(), name='hostel-staff-occupancy'),
    path('staff/attendance/', StaffHostelAttendanceView.as_view(), name='hostel-staff-attendance'),
    path('staff/incidents/', StaffHostelIncidentView.as_view(), name='hostel-staff-incidents'),
    path('staff/in-out/', StaffHostelInOutView.as_view(), name='hostel-staff-in-out'),

    # Student
    path('student/my-hostel/', StudentMyHostelView.as_view(), name='hostel-student-my-hostel'),
]
