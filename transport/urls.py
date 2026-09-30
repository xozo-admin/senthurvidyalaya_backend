from django.urls import path
from .views import (
    AdminVehicleView, 
    AdminVehicleDetailView, 
    AdminRouteView, 
    AdminBulkAllocationView, 
    AdminPassengerListView, 
    DriverMyPassengersView, 
    MyBusInfoView,
    AdminAssignDriverView,
    BusAttendanceView,
    AdminStopManageView,
    UserBusAttendanceHistoryView,
    DriverExpenseView,
    AdminExpenseView,
    AdminUserHistoryView,
    AdminBusDateView,
    AdminBusListView,
    ActiveBusListView,
    DriverDashboardView,
    DriverRouteView,
)

urlpatterns = [
    # ==================================
    # 1. ADMIN - VEHICLE MANAGEMENT
    # ==================================
    # GET (List), POST (Create)
    path('admin/vehicles/', AdminVehicleView.as_view()),
    
    # DELETE (Delete Bus by ID)
    path('admin/vehicles/<int:pk>/', AdminVehicleDetailView.as_view()),

    # ==================================
    # 2. ADMIN - ROUTE & DRIVER
    # ==================================
    # GET, POST, PUT, DELETE (Query Param: ?bus_number=12)
    path('admin/routes/', AdminRouteView.as_view()),

    # GET, POST, PUT, DELETE (Query Param: ?bus_number=12)
    path('admin/assign-driver/', AdminAssignDriverView.as_view()),

    # ==================================
    # 3. ADMIN - PASSENGER ALLOCATION
    # ==================================
    # GET, POST (Assign), DELETE (Remove) - (Query Param: ?bus_number=12)
    path('admin/allocation/', AdminBulkAllocationView.as_view()), 
    # Note: I renamed 'assign-bulk' to 'allocation' because it now handles GET and DELETE too.

    # GET Only (View List) - (Query Param: ?bus_number=12)
    path('admin/passengers-list/', AdminPassengerListView.as_view()),

    # ==================================
    # 4. DRIVER APP
    # ==================================
    # GET (View My Bus Passengers)
    path('driver/my-passengers/', DriverMyPassengersView.as_view()),

    # GET (View Attendance Sheet), POST (Mark Attendance)
    path('driver/attendance/', BusAttendanceView.as_view(), name='bus-attendance'),

    # ==================================
    # 5. STUDENT/STAFF APP
    # ==================================
    # GET (Track My Bus & Route Info)
    path('my-bus/', MyBusInfoView.as_view()),

    # 7. Single Stop Management (Edit/Delete ONE Stop)
    path('admin/stops/', AdminStopManageView.as_view()),

    path('user/history/', UserBusAttendanceHistoryView.as_view(), name='user-bus-history'),


    # Driver: Upload/Edit Proofs
    path('driver/expenses/', DriverExpenseView.as_view(), name='driver-expenses'),

    # Admin: View/Filter Proofs
    path('admin/expenses/', AdminExpenseView.as_view(), name='admin-expenses'),

    # Admin: View Full Year History
    path('transport-admin/user-history/', AdminUserHistoryView.as_view(), name='admin-user-history'),

    #for specific date
    path('transport-admin/bus-date-view/', AdminBusDateView.as_view(), name='admin-bus-date-view'),

     # admin bus list
    path('transport-admin/bus-list/', AdminBusListView.as_view(), name='admin-bus-list'),

    #active buses 
    path('active-buses/', ActiveBusListView.as_view(), name='active-buses'),

    # ... your other transport urls ...
    path('driver/dashboard/', DriverDashboardView.as_view(), name='driver-dashboard'),

    # URL for the Driver to see their specific route and stops
    path('driver/my-route/', DriverRouteView.as_view(), name='driver-my-route'),
]