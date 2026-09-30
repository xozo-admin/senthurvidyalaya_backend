from django.urls import path
from .views import HolidayManageView, PublicHolidayView, AdminHolidayPaginatedView

urlpatterns = [
    path('admin/manage/', HolidayManageView.as_view()),
    path('admin/manage/paginated/', AdminHolidayPaginatedView.as_view()),
    # 2. Public: View Holidays (Smart Filter for Student/Teacher/Staff)
    path('list/', PublicHolidayView.as_view(), name='public-holiday-list'),
]
