from django.urls import path
from .views import AdminInventoryView, AdminInventoryPaginatedView, StaffInventoryActionView

urlpatterns = [
    # Admin
    path('admin/manage/', AdminInventoryView.as_view()),
    path('admin/manage/paginated/', AdminInventoryPaginatedView.as_view()),
    
    # Staff
    path('staff/actions/', StaffInventoryActionView.as_view()),
]
