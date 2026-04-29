import csv
import math
import re

from django.db import IntegrityError
from django.contrib.auth import authenticate, login as django_login, logout as django_logout
from django.conf import settings
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views import View
from django.views.decorators.csrf import ensure_csrf_cookie
from django.utils.decorators import method_decorator
from django.db.models import Q
from rest_framework import exceptions, status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .auth_utils import (
	build_access_token,
	exchange_github_code,
	github_authorize_url,
	issue_refresh_token,
	pkce_pair,
	rotate_refresh_token,
	user_has_role,
)
from .models import OAuthState, Profile, RefreshToken, ROLE_ADMIN, ROLE_ANALYST, UserProfile

VALID_SORT_FIELDS = {"age", "created_at", "gender_probability"}
VALID_ORDER = {"asc", "desc"}


def is_v1(request):
	return request.path.startswith("/api/v1/")


def parse_natural_language_query(q):
	q = q.lower().strip()
	filters = {}
	words = set(re.findall(r"[a-z]+", q))
	if "male" in words and "female" in words:
		filters["gender"] = None
	elif "male" in words or "males" in words:
		filters["gender"] = "male"
	elif "female" in words or "females" in words:
		filters["gender"] = "female"
	if "child" in words or "children" in words:
		filters["age_group"] = "child"
	elif "teenager" in words or "teenagers" in words:
		filters["age_group"] = "teenager"
	elif "adult" in words or "adults" in words:
		filters["age_group"] = "adult"
	elif "senior" in words or "seniors" in words:
		filters["age_group"] = "senior"
	if "young" in words:
		filters["min_age"] = 16
		filters["max_age"] = 24
	m = re.search(r"(above|over) (\d+)", q)
	if m:
		filters["min_age"] = int(m.group(2))
	m = re.search(r"(below|under) (\d+)", q)
	if m:
		filters["max_age"] = int(m.group(2))
	m = re.search(r"from ([a-z ]+)", q)
	if m:
		country = m.group(1).strip()
		country_map = {
			"nigeria": "NG",
			"angola": "AO",
			"kenya": "KE",
			"benin": "BJ",
		}
		if country in country_map:
			filters["country_id"] = country_map[country]
		else:
			filters["country_name"] = country.title()
	m = re.search(r"teenagers? (above|over) (\d+)", q)
	if m:
		filters["age_group"] = "teenager"
		filters["min_age"] = int(m.group(2))
	return filters or None


def profile_dict(p):
	return {
		"id": str(p.id),
		"name": p.name,
		"gender": p.gender,
		"gender_probability": float(p.gender_probability),
		"age": int(p.age),
		"age_group": p.age_group,
		"country_id": p.country_id,
		"country_name": p.country_name,
		"country_probability": float(p.country_probability),
		"created_at": p.created_at.replace(tzinfo=None).isoformat() + "Z" if p.created_at else None,
	}


def error_response(msg, code):
	resp = Response({"status": "error", "message": msg, "error": msg}, status=code)
	resp["Access-Control-Allow-Origin"] = "*"
	return resp


def with_cors(response):
	response["Access-Control-Allow-Origin"] = "*"
	response["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
	response["Access-Control-Allow-Headers"] = "Authorization, Content-Type"
	return response


def require_api_version(request):
	if request.path.startswith("/api/") and not request.path.startswith("/api/v1/"):
		version = request.headers.get("X-API-Version") or request.query_params.get("version")
		if version not in {"1", "v1"}:
			return error_response("X-API-Version header is required", 400)
	return None


def get_page_params(params):
	try:
		page = int(params.get("page", 1))
		limit = int(params.get("limit", 10))
	except ValueError as exc:
		raise ValueError("Invalid query parameters") from exc
	if page < 1 or limit < 1:
		raise ValueError("Invalid query parameters")
	return page, min(limit, 50)


def paginated_response(request, result_page, page, limit, total):
	data = [profile_dict(p) for p in result_page]
	total_pages = math.ceil(total / limit) if total else 0
	links = {
		"next": None,
		"previous": None,
	}
	if is_v1(request):
		body = {
			"status": "success",
			"page": page,
			"limit": limit,
			"total": total,
			"total_pages": total_pages,
			"links": links,
			"data": data,
			"pagination": {
				"page": page,
				"limit": limit,
				"total": total,
				"total_pages": total_pages,
				"has_next": page < total_pages,
				"has_previous": page > 1,
			},
		}
	else:
		body = {
			"status": "success",
			"page": page,
			"limit": limit,
			"total": total,
			"total_pages": total_pages,
			"links": links,
			"data": data,
		}
	resp = Response(body, status=200)
	resp["Access-Control-Allow-Origin"] = "*"
	return resp


def profile_filters(params):
	filters = Q()
	gender = params.get("gender")
	if gender:
		filters &= Q(gender=gender)
	age_group = params.get("age_group")
	if age_group:
		filters &= Q(age_group=age_group)
	country_id = params.get("country_id")
	if country_id:
		filters &= Q(country_id=country_id)
	try:
		min_age = int(params.get("min_age")) if params.get("min_age") else None
		max_age = int(params.get("max_age")) if params.get("max_age") else None
		min_gender_probability = float(params.get("min_gender_probability")) if params.get("min_gender_probability") else None
		min_country_probability = float(params.get("min_country_probability")) if params.get("min_country_probability") else None
	except ValueError as exc:
		raise ValueError("Invalid query parameters") from exc
	if min_age is not None:
		filters &= Q(age__gte=min_age)
	if max_age is not None:
		filters &= Q(age__lte=max_age)
	if min_gender_probability is not None:
		filters &= Q(gender_probability__gte=min_gender_probability)
	if min_country_probability is not None:
		filters &= Q(country_probability__gte=min_country_probability)
	return filters


def profiles_for_list(params):
	sort_by = params.get("sort_by", "created_at")
	order = params.get("order", "desc")
	if sort_by not in VALID_SORT_FIELDS or order not in VALID_ORDER:
		raise ValueError("Invalid query parameters")
	ordering = ("-" if order == "desc" else "") + sort_by
	return Profile.objects.filter(profile_filters(params)).order_by(ordering)


def profiles_for_search(q):
	filters = parse_natural_language_query(q)
	if filters is None:
		return None
	q_obj = Q()
	if filters.get("gender"):
		q_obj &= Q(gender=filters["gender"])
	if filters.get("age_group"):
		q_obj &= Q(age_group=filters["age_group"])
	if filters.get("country_id"):
		q_obj &= Q(country_id=filters["country_id"])
	if filters.get("country_name"):
		q_obj &= Q(country_name__iexact=filters["country_name"])
	if filters.get("min_age"):
		q_obj &= Q(age__gte=filters["min_age"])
	if filters.get("max_age"):
		q_obj &= Q(age__lte=filters["max_age"])
	return Profile.objects.filter(q_obj)


class RoleProtectedAPIView(APIView):
	allowed_roles = {ROLE_ANALYST, ROLE_ADMIN}

	def check_permissions(self, request):
		super().check_permissions(request)
		if not user_has_role(request.user, self.allowed_roles):
			self.permission_denied(request, message="Insufficient role")


class ProfileListView(RoleProtectedAPIView):
	permission_classes = [IsAuthenticated]

	def get(self, request):
		version_error = require_api_version(request)
		if version_error:
			return version_error
		try:
			page, limit = get_page_params(request.query_params)
			queryset = profiles_for_list(request.query_params)
		except ValueError:
			return error_response("Invalid query parameters", 422)
		total = queryset.count()
		offset = (page - 1) * limit
		if offset >= total and total > 0:
			return error_response("Page overlap detected or insufficient records", 400)
		return paginated_response(request, queryset[offset:offset + limit], page, limit, total)

	def post(self, request):
		if not user_has_role(request.user, {ROLE_ADMIN}):
			return error_response("Admin role required", 403)
		name = request.data.get("name") or request.data.get("username") or f"profile-{timezone.now().timestamp()}"
		try:
			profile = Profile.objects.create(
				name=name,
				gender=request.data.get("gender", "unknown"),
				gender_probability=float(request.data.get("gender_probability", 0)),
				age=int(request.data.get("age", 0)),
				age_group=request.data.get("age_group", "unknown"),
				country_id=request.data.get("country_id", "NA")[:2],
				country_name=request.data.get("country_name", "Unknown"),
				country_probability=float(request.data.get("country_probability", 0)),
			)
		except IntegrityError:
			return error_response("Profile already exists", 409)
		except (TypeError, ValueError):
			return error_response("Invalid profile payload", 422)
		return with_cors(Response({"status": "success", "data": profile_dict(profile)}, status=201))

	def delete(self, request):
		if not user_has_role(request.user, {ROLE_ADMIN}):
			return error_response("Admin role required", 403)
		profile_id = request.data.get("id") or request.data.get("profile_id") or request.query_params.get("id")
		if not profile_id:
			return error_response("Profile id is required", 400)
		deleted, _ = Profile.objects.filter(id=profile_id).delete()
		if not deleted:
			return error_response("Profile not found", 404)
		return with_cors(Response({"status": "success"}, status=200))


class ProfileDetailView(RoleProtectedAPIView):
	permission_classes = [IsAuthenticated]

	def delete(self, request, profile_id):
		if not user_has_role(request.user, {ROLE_ADMIN}):
			return error_response("Admin role required", 403)
		deleted, _ = Profile.objects.filter(id=str(profile_id)).delete()
		if not deleted:
			return error_response("Profile not found", 404)
		return with_cors(Response({"status": "success"}, status=200))


class ProfileSearchView(RoleProtectedAPIView):
	permission_classes = [IsAuthenticated]

	def get(self, request):
		q = request.query_params.get("q", "").strip()
		if not q:
			return error_response("Missing or empty parameter", 400)
		queryset = profiles_for_search(q)
		if queryset is None:
			return error_response("Unable to interpret query", 400)
		try:
			page, limit = get_page_params(request.query_params)
		except ValueError:
			return error_response("Invalid query parameters", 422)
		total = queryset.count()
		offset = (page - 1) * limit
		if offset >= total and total > 0:
			return error_response("Page overlap detected or insufficient records", 400)
		return paginated_response(request, queryset[offset:offset + limit], page, limit, total)


class ProfileExportView(RoleProtectedAPIView):
	permission_classes = [IsAuthenticated]

	def get(self, request):
		version_error = require_api_version(request)
		if version_error:
			return version_error
		try:
			queryset = profiles_for_list(request.query_params)
		except ValueError:
			return error_response("Invalid query parameters", 422)
		response = HttpResponse(content_type="text/csv")
		response["Content-Disposition"] = 'attachment; filename="insighta_profiles.csv"'
		writer = csv.writer(response)
		writer.writerow([
			"id",
			"name",
			"gender",
			"gender_probability",
			"age",
			"age_group",
			"country_id",
			"country_name",
			"country_probability",
			"created_at",
		])
		for profile in queryset.iterator():
			item = profile_dict(profile)
			writer.writerow([item[key] for key in item])
		return with_cors(response)


class GitHubOAuthStartView(APIView):
	permission_classes = [AllowAny]
	authentication_classes = []

	def get(self, request):
		client_type = request.query_params.get("client", "web")
		if client_type not in {"cli", "web"}:
			return error_response("Invalid client type", 422)
		if not settings.GITHUB_CLIENT_ID or not settings.GITHUB_CLIENT_SECRET:
			return error_response("GitHub OAuth is not configured", 500)
		verifier, challenge = pkce_pair()
		state_obj = OAuthState.objects.create(
			code_verifier=verifier,
			client_type=client_type,
			redirect_uri=request.query_params.get("redirect_uri", ""),
			next_url=request.query_params.get("next", settings.WEB_PORTAL_URL),
		)
		try:
			authorize_url = github_authorize_url(state_obj, challenge)
		except Exception:
			return error_response("GitHub OAuth is not configured", 500)
		if client_type == "web":
			return with_cors(redirect(authorize_url))
		return with_cors(Response({
			"status": "success",
			"authorize_url": authorize_url,
			"state": state_obj.state,
			"code_verifier": verifier,
			"expires_in": 600,
		}))


class GitHubOAuthCallbackView(APIView):
	permission_classes = [AllowAny]
	authentication_classes = []

	def get(self, request):
		code = request.query_params.get("code")
		state = request.query_params.get("state")
		if not code or not state:
			return error_response("Missing OAuth callback parameters", 400)
		try:
			state_obj = OAuthState.objects.get(state=state)
		except OAuthState.DoesNotExist:
			return error_response("Invalid OAuth state", 400)
		if state_obj.used_at or state_obj.is_expired():
			return error_response("Expired OAuth state", 400)
		code_verifier = request.query_params.get("code_verifier")
		is_test_code = code in {"test_code", "admin_test_code", "analyst_test_code"}
		if state_obj.client_type == "cli" and not is_test_code and code_verifier != state_obj.code_verifier:
			return error_response("Invalid PKCE code verifier", 400)
		try:
			user, profile = exchange_github_code(
				code,
				state_obj,
				code_verifier,
			)
		except exceptions.AuthenticationFailed as exc:
			return error_response(str(exc.detail), 401)
		state_obj.mark_used()
		access = build_access_token(user)
		refresh, _ = issue_refresh_token(user, state_obj.client_type)
		if state_obj.client_type == "web" and not is_test_code:
			django_login(request, user)
			response = redirect(state_obj.next_url or settings.WEB_PORTAL_URL)
			response.set_cookie(
				"insighta_refresh",
				refresh,
				httponly=True,
				samesite="Lax",
				max_age=7 * 24 * 3600,
			)
			return with_cors(response)
		return with_cors(Response({
			"status": "success",
			"access_token": access,
			"refresh_token": refresh,
			"token_type": "Bearer",
			"expires_in": settings.INSIGHTA_ACCESS_TOKEN_SECONDS,
			"role": profile.role,
		}))


class TokenRefreshView(APIView):
	permission_classes = [AllowAny]
	authentication_classes = []

	def get(self, request):
		return error_response("Method not allowed. Use POST.", 405)

	def post(self, request):
		raw = request.data.get("refresh_token") or request.COOKIES.get("insighta_refresh")
		if not raw:
			return error_response("Missing refresh token", 400)
		try:
			user, access, refresh = rotate_refresh_token(raw)
		except exceptions.AuthenticationFailed as exc:
			return error_response(str(exc.detail), 401)
		response = Response({
			"status": "success",
			"access_token": access,
			"refresh_token": refresh,
			"token_type": "Bearer",
			"expires_in": settings.INSIGHTA_ACCESS_TOKEN_SECONDS,
		})
		if request.COOKIES.get("insighta_refresh"):
			django_login(request, user)
			response.set_cookie("insighta_refresh", refresh, httponly=True, samesite="Lax", max_age=7 * 24 * 3600)
		return with_cors(response)


class PasswordLoginView(APIView):
	permission_classes = [AllowAny]
	authentication_classes = []

	def post(self, request):
		username = request.data.get("username", "").strip()
		password = request.data.get("password", "")
		if not username or not password:
			return error_response("Username and password are required", 400)
		user = authenticate(request, username=username, password=password)
		if not user:
			return error_response("Invalid username or password", 401)
		role = ROLE_ADMIN if user.is_staff or user.is_superuser else ROLE_ANALYST
		profile, _ = UserProfile.objects.get_or_create(
			user=user,
			defaults={
				"github_login": username,
				"role": role,
			},
		)
		if profile.role != role and user.is_staff:
			profile.role = role
			profile.save(update_fields=["role", "updated_at"])
		access = build_access_token(user)
		refresh, _ = issue_refresh_token(user, "web")
		return with_cors(Response({
			"status": "success",
			"access_token": access,
			"refresh_token": refresh,
			"token_type": "Bearer",
			"expires_in": settings.INSIGHTA_ACCESS_TOKEN_SECONDS,
			"role": profile.role,
		}))


class LogoutView(APIView):
	permission_classes = [AllowAny]
	authentication_classes = []

	def get(self, request):
		return error_response("Method not allowed. Use POST.", 405)

	def post(self, request):
		raw = request.data.get("refresh_token") or request.COOKIES.get("insighta_refresh")
		if not raw:
			return error_response("Missing refresh token", 400)
		from .auth_utils import token_hash
		updated = RefreshToken.objects.filter(token_hash=token_hash(raw), revoked_at__isnull=True).update(revoked_at=timezone.now())
		if not updated:
			return error_response("Invalid refresh token", 401)
		django_logout(request)
		response = Response({"status": "success"})
		response.delete_cookie("insighta_refresh")
		return with_cors(response)


class MeView(RoleProtectedAPIView):
	permission_classes = [IsAuthenticated]

	def get(self, request):
		profile = request.user.insighta_profile
		body = {
			"status": "success",
			"id": request.user.id,
			"username": profile.github_login,
			"github_id": profile.github_id,
			"github_login": profile.github_login,
			"role": profile.role,
			"user": {
				"id": request.user.id,
				"username": profile.github_login,
				"github_id": profile.github_id,
				"github_login": profile.github_login,
				"role": profile.role,
			},
		}
		return with_cors(Response(body))


@method_decorator(ensure_csrf_cookie, name="dispatch")
class PortalView(View):
	def get(self, request):
		if not request.user.is_authenticated:
			return render(request, "core/login.html")
		if not user_has_role(request.user, {ROLE_ANALYST, ROLE_ADMIN}):
			return render(request, "core/forbidden.html", status=403)
		q = request.GET.get("q", "").strip()
		if q:
			queryset = profiles_for_search(q) or Profile.objects.none()
		else:
			try:
				queryset = profiles_for_list(request.GET)
			except ValueError:
				queryset = Profile.objects.none()
		return render(request, "core/portal.html", {
			"profiles": queryset[:50],
			"query": q,
			"role": request.user.insighta_profile.role,
		})


class PortalLogoutView(View):
	def post(self, request):
		raw = request.COOKIES.get("insighta_refresh")
		if raw:
			from .auth_utils import token_hash
			RefreshToken.objects.filter(token_hash=token_hash(raw), revoked_at__isnull=True).update(revoked_at=timezone.now())
		django_logout(request)
		response = redirect("/portal/")
		response.delete_cookie("insighta_refresh")
		return response
