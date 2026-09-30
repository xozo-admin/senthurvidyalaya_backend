from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import AllowAny
from rest_framework import status
from django.core.mail import get_connection, send_mail
from django.conf import settings
from django.contrib.auth import get_user_model
import random
import logging

from .models import PasswordResetOTP

User = get_user_model()
logger = logging.getLogger(__name__)


def _find_user(username_input):
    username_input = str(username_input or "").strip()
    if not username_input:
        return None

    user = User.objects.filter(username__iexact=username_input).first()
    if user:
        return user

    return User.objects.filter(phone=username_input).first()


def _mask_email(email):
    email = str(email or "").strip()
    if not email:
        return ""

    if "@" not in email:
        return f"{email[0]}****" if email else ""

    local, domain = email.split("@", 1)
    first_char = local[0] if local else "*"
    return f"{first_char}****@{domain}"

# ==========================================
# 1. REQUEST OTP (Send Code via Email)
# ==========================================
class RequestPasswordResetOTP(APIView):
    permission_classes = [AllowAny] 

    def post(self, request):
        # NOW USING 'username' KEY DIRECTLY
        username_input = str(request.data.get('username') or "").strip()
        method = str(request.data.get('method') or 'email').strip().lower()

        if not username_input:
            return Response({"error": "Please provide your Username"}, 400)

        # Try finding by username first, then phone (in case they typed phone in username field)
        user = _find_user(username_input)
        
        if not user:
            return Response({"error": "User not found"}, 404)

        # Generate 6-digit OTP
        otp = str(random.randint(100000, 999999))

        # Save OTP to DB
        PasswordResetOTP.objects.update_or_create(
            user=user,
            defaults={'otp_code': otp}
        )

        # --- SEND VIA EMAIL ---
        if method == 'email':
            if not user.email:
                return Response({"error": "No email linked to this account."}, 400)
            
            masked_email = _mask_email(user.email)

            try:
                connection = get_connection(timeout=getattr(settings, "EMAIL_TIMEOUT", 8))
                send_mail(
                    subject="Your Password Reset Code",
                    message=f"Hello {user.username},\n\nYour OTP is: {otp}\n\nValid for 10 minutes.",
                    from_email=settings.DEFAULT_FROM_EMAIL,
                    recipient_list=[user.email],
                    fail_silently=False,
                    connection=connection,
                )
                return Response({
                    "message": f"OTP sent to {masked_email}", 
                    "otp_sent_to": "email"
                })
            except Exception as e:
                logger.exception("Failed to send password reset OTP email for user %s", user.username)

                if getattr(settings, "PASSWORD_RESET_DEBUG_OTP_ON_EMAIL_FAILURE", False):
                    return Response({
                        "message": "Email delivery failed, but a development OTP was generated.",
                        "otp_sent_to": "development",
                        "debug_otp": otp,
                        "error": f"Failed to send email: {str(e)}",
                    })

                return Response(
                    {
                        "error": "Unable to send password reset OTP email right now. Please check email settings and try again.",
                        "detail": str(e) if getattr(settings, "DEBUG", False) else "",
                    },
                    status=status.HTTP_503_SERVICE_UNAVAILABLE,
                )

        # --- SEND VIA SMS ---
        elif method == 'sms':
            masked_phone = "******" + str(user.phone)[-4:] if user.phone else "Unknown"
            print(f"--- FAKE SMS: OTP {otp} to {user.phone} ---") 
            return Response({
                "message": f"OTP sent to {masked_phone}", 
                "otp_sent_to": "sms"
            })

        return Response({"error": "Invalid method"}, 400)


# ==========================================
# 2. VERIFY OTP ONLY (For Screen 2 - "Next" Button)
# ==========================================
class VerifyOTPOnly(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        username_input = str(request.data.get('username') or "").strip()
        otp_input = request.data.get('otp')

        user = _find_user(username_input)
        
        if not user:
            return Response({"error": "User not found"}, 404)

        try:
            otp_record = PasswordResetOTP.objects.get(user=user)
        except PasswordResetOTP.DoesNotExist:
            return Response({"error": "No OTP request found."}, 400)

        if otp_record.otp_code != str(otp_input):
            return Response({"error": "Invalid OTP"}, 400)
        
        if not otp_record.is_valid():
            return Response({"error": "OTP Expired"}, 400)

        return Response({"message": "OTP Verified", "status": "success"})


# ==========================================
# 3. CONFIRM & CHANGE PASSWORD (Screen 3 - "Submit")
# ==========================================
class ResetPasswordConfirm(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        username_input = str(request.data.get('username') or "").strip()
        otp_input = request.data.get('otp')
        new_password = request.data.get('new_password')
        confirm_password = request.data.get('confirm_password')

        if new_password != confirm_password:
            return Response({"error": "Passwords do not match"}, 400)

        user = _find_user(username_input)
        
        if not user:
            return Response({"error": "User not found"}, 404)

        # Check OTP (Again for security)
        try:
            otp_record = PasswordResetOTP.objects.get(user=user)
        except PasswordResetOTP.DoesNotExist:
            return Response({"error": "No OTP found. Request a new one."}, 400)

        if otp_record.otp_code != str(otp_input):
            return Response({"error": "Invalid OTP"}, 400)

        if not otp_record.is_valid():
            return Response({"error": "OTP Expired"}, 400)

        # CHANGE PASSWORD
        user.set_password(new_password)
        user.save()

        otp_record.delete() # Security: Delete OTP so it can't be used twice

        return Response({"message": "Password changed successfully!"})
