from django.http import HttpResponse

class SecurityHeadersMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response
    def __call__(self, request):
        if request.method in {"POST", "PUT", "PATCH"} and not request.META.get("CONTENT_LENGTH"):
            return HttpResponse("Требуется Content-Length", status=411)
        try:
            size = int(request.META.get("CONTENT_LENGTH") or 0)
        except ValueError:
            return HttpResponse("Некорректный запрос", status=400)
        if size < 0 or size > 6 * 1024 * 1024:
            return HttpResponse("Размер запроса превышает 6 МБ", status=413)
        response = self.get_response(request)
        response["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; font-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
        response["Permissions-Policy"] = "camera=(), microphone=(), geolocation=(), payment=()"
        response["Cache-Control"] = "no-store"
        return response
