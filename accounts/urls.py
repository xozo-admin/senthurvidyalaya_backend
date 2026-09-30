from django.urls import path
from .views_otp import RequestPasswordResetOTP, ResetPasswordConfirm
from .views import (
    AdminRegisterView, 
    SuperAdminRegisterView,
    SchoolRegisterView, 
    LoginView, 
    VerifyOTPView,
)
# 2. Import the NEW OTP views from views_otp.py
from .views_otp import (
    RequestPasswordResetOTP, 
    VerifyOTPOnly, 
    ResetPasswordConfirm
)

urlpatterns = [
    # --- Registration ---
    # REMOVED "setup/" from here so the URL is cleaner
    path("admin-register/", AdminRegisterView.as_view(), name='admin_register'),
    path("super-admin-register/", SuperAdminRegisterView.as_view(), name='super_admin_register'),
    path("school-register/", SchoolRegisterView.as_view(), name='school_register'),

    # --- Login & Authentication ---
    path("login/", LoginView.as_view(), name='login'),
    path("verify-otp/", VerifyOTPView.as_view(), name='verify_otp'),


    path('password-reset/request/', RequestPasswordResetOTP.as_view(), name='request-otp'),
    path('password-reset/confirm/', ResetPasswordConfirm.as_view(), name='confirm-reset'),
    path('password-reset/verify-otp/', VerifyOTPOnly.as_view(), name='verify-otp-only'),
]
