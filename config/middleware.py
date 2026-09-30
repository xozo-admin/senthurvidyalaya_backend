# config/middleware.py
import json
from django.utils.deprecation import MiddlewareMixin
from django.conf import settings
from rest_framework.authtoken.models import Token
from config.encryption import decrypt_aes_gcm, encrypt_aes_gcm

class DynamicSecurityMiddleware(MiddlewareMixin):
    def process_request(self, request):
        # 1. THE SWITCH: If Encryption is OFF, stop here.
        if not getattr(settings, 'ENABLE_ENCRYPTION', False):
            return None 

        # 2. IGNORING PATHS:
        # These endpoints must handle plain text because the user 
        # doesn't have a session key yet (Login/Register).
        ignored_paths = [
            '/api/accounts/login/',
            '/api/accounts/register/admin/',
            '/api/accounts/register/school/',
            '/api/accounts/verify-otp/',
        ]
        
        if request.path in ignored_paths:
            return None

        # 3. For all other APIs, try to decrypt
        if request.path.startswith('/api/') and request.body:
            try:
                # We need to find the user's Session Key using their Token
                auth_header = request.headers.get('Authorization')
                if auth_header:
                    token_key = auth_header.split(' ')[1]
                    token = Token.objects.get(key=token_key)
                    user = token.user
                    
                    if hasattr(user, 'session_key') and user.session_key:
                        decrypted_data = decrypt_aes_gcm(request.body, user.session_key)
                        if decrypted_data:
                            request._body = json.dumps(decrypted_data).encode('utf-8')
            except Exception:
                pass 

    def process_response(self, request, response):
        # 1. THE SWITCH: If OFF, return normal plain text
        if not getattr(settings, 'ENABLE_ENCRYPTION', False):
            return response

        # 2. IGNORING PATHS:
        ignored_paths = [
            '/api/accounts/login/',
            '/api/accounts/register/admin/',
            '/api/accounts/register/school/',
            '/api/accounts/verify-otp/',
        ]

        if request.path in ignored_paths:
            return response

        # 3. Encrypt Response
        if request.path.startswith('/api/'):
             # Only encrypt if we have a user and data to encrypt
             if hasattr(response, 'data') and hasattr(request, 'user'):
                try:
                    if hasattr(request.user, 'session_key') and request.user.session_key:
                        encrypted_string = encrypt_aes_gcm(response.data, request.user.session_key)
                        
                        response.content = json.dumps({"response": encrypted_string})
                        # Update Content-Length because the size changed
                        response['Content-Length'] = str(len(response.content))
                except Exception:
                    pass
                    
        return response