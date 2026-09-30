# transport/routing.py
from django.urls import re_path
from . import consumers

websocket_urlpatterns = [
    # Example: ws://127.0.0.1:8000/ws/track/5/
    re_path(r'ws/track/(?P<route_id>\w+)/$', consumers.BusTrackingConsumer.as_asgi()),
    # Admin live stream for all active buses
    re_path(r'ws/track/admin/live/$', consumers.AdminLiveTrackingConsumer.as_asgi()),
]
