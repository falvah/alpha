from django.conf import settings
from .models import Notification

def navigation(request):
    user = request.user
    return {"sandbox_mode": settings.SANDBOX_MODE, "unread_count": Notification.objects.filter(user=user, read=False).count() if user.is_authenticated else 0,
            "nav": request.resolver_match.url_name if request.resolver_match else ""}
