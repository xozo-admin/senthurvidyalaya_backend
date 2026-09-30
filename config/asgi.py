import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.core.asgi import get_asgi_application
from channels.routing import ProtocolTypeRouter, URLRouter
# Import your new middleware
from config.token_auth_middleware import TokenAuthMiddleware 
import transport.routing

application = ProtocolTypeRouter({
    "http": get_asgi_application(),
    "websocket": TokenAuthMiddleware(  # <--- WRAP ROUTER WITH THIS
        URLRouter(
            transport.routing.websocket_urlpatterns
        )
    ),
})