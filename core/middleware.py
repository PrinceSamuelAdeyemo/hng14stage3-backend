import time

from django.core.cache import cache
from django.http import JsonResponse

from .models import RequestLog


def client_ip(request):
	forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
	if forwarded:
		return forwarded.split(",")[0].strip()
	return request.META.get("REMOTE_ADDR")


class RateLimitMiddleware:
	def __init__(self, get_response):
		self.get_response = get_response

	def __call__(self, request):
		if request.path.startswith("/api/") or request.path.startswith("/auth/"):
			ip = client_ip(request) or "unknown"
			is_oauth_start = request.path in {
				"/auth/github",
				"/auth/github/",
				"/auth/github/start",
				"/auth/github/start/",
				"/api/auth/github",
				"/api/auth/github/",
				"/api/v1/auth/github",
				"/api/v1/auth/github/",
				"/api/auth/github/start",
				"/api/auth/github/start/",
				"/api/v1/auth/github/start",
				"/api/v1/auth/github/start/",
			}
			limit = 10 if is_oauth_start else 120
			key = f"rl:{ip}:{request.path.rstrip('/')}:{int(time.time() // 60)}"
			count = cache.get(key, 0) + 1
			cache.set(key, count, timeout=70)
			if count > limit:
				response = JsonResponse(
					{"status": "error", "message": "Rate limit exceeded"},
					status=429,
				)
				response["Access-Control-Allow-Origin"] = "*"
				return response
		return self.get_response(request)


class RequestLoggingMiddleware:
	def __init__(self, get_response):
		self.get_response = get_response

	def __call__(self, request):
		start = time.monotonic()
		response = self.get_response(request)
		if request.path.startswith("/api/"):
			try:
				RequestLog.objects.create(
					method=request.method,
					path=request.path[:512],
					status_code=response.status_code,
					user=request.user if getattr(request, "user", None) and request.user.is_authenticated else None,
					ip_address=client_ip(request),
					duration_ms=int((time.monotonic() - start) * 1000),
				)
			except Exception:
				pass
		return response
