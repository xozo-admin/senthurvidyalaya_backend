from urllib.parse import parse_qs
from channels.db import database_sync_to_async
from rest_framework.authtoken.models import Token
from django.contrib.auth.models import AnonymousUser

# 1. This function MUST be here so the class can find it
@database_sync_to_async
def get_user_from_token(token_key):
    try:
        # We look up the token in the DRF Token table
        token = Token.objects.get(key=token_key)
        return token.user
    except (Token.DoesNotExist, Exception):
        return AnonymousUser()

class TokenAuthMiddleware:
    def __init__(self, inner):
        self.inner = inner

    async def __call__(self, scope, receive, send):
        # Extract headers and look for 'authorization'
        headers = dict(scope.get('headers', []))
        auth_header = headers.get(b'authorization', b'').decode()

        token_key = None

        # Priority 1: Check Authorization Header (For Postman/Mobile)
        if auth_header.startswith('Token '):
            token_key = auth_header.split(' ')[1]

        # Priority 2: Check URL Query String (For PieSocket/Web)
        if not token_key:
            query_string = scope.get("query_string", b"").decode()
            params = parse_qs(query_string)
            token_list = params.get("token")
            if token_list:
                token_key = token_list[0]

        # 2. Authenticate the User
        if token_key:
            # This calls the function defined at the top
            scope["user"] = await get_user_from_token(token_key)
        else:
            scope["user"] = AnonymousUser()

        return await self.inner(scope, receive, send)