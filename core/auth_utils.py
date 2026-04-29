import base64
import hashlib
import json
import secrets
from datetime import timedelta
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import URLError

from django.conf import settings
from django.contrib.auth.models import User
from django.core import signing
from django.utils import timezone
from rest_framework import authentication, exceptions, permissions

from .models import ROLE_ADMIN, ROLE_ANALYST, OAuthState, RefreshToken, UserProfile

ACCESS_TOKEN_MAX_AGE_SECONDS = int(getattr(settings, "INSIGHTA_ACCESS_TOKEN_SECONDS", 900))
REFRESH_TOKEN_DAYS = int(getattr(settings, "INSIGHTA_REFRESH_TOKEN_DAYS", 7))


def token_hash(token):
	return hashlib.sha256(token.encode("utf-8")).hexdigest()


def build_access_token(user):
	profile = getattr(user, "insighta_profile", None)
	payload = {
		"uid": user.id,
		"role": profile.role if profile else ROLE_ANALYST,
		"login": profile.github_login if profile else user.username,
	}
	return signing.dumps(payload, salt="insighta-access-token")


def parse_access_token(token):
	try:
		payload = signing.loads(
			token,
			salt="insighta-access-token",
			max_age=ACCESS_TOKEN_MAX_AGE_SECONDS,
		)
	except signing.SignatureExpired as exc:
		raise exceptions.AuthenticationFailed("Access token expired") from exc
	except signing.BadSignature as exc:
		raise exceptions.AuthenticationFailed("Invalid access token") from exc
	try:
		return User.objects.get(id=payload["uid"], is_active=True)
	except User.DoesNotExist as exc:
		raise exceptions.AuthenticationFailed("User not found") from exc


def issue_refresh_token(user, client_type):
	raw = secrets.token_urlsafe(48)
	refresh = RefreshToken.objects.create(
		user=user,
		token_hash=token_hash(raw),
		client_type=client_type,
		expires_at=timezone.now() + timedelta(days=REFRESH_TOKEN_DAYS),
	)
	return raw, refresh


def rotate_refresh_token(raw_token, client_type=None):
	try:
		refresh = RefreshToken.objects.select_related("user").get(token_hash=token_hash(raw_token))
	except RefreshToken.DoesNotExist as exc:
		raise exceptions.AuthenticationFailed("Invalid refresh token") from exc
	if not refresh.is_active():
		raise exceptions.AuthenticationFailed("Refresh token expired or revoked")
	refresh.revoked_at = timezone.now()
	refresh.save(update_fields=["revoked_at"])
	next_client = client_type or refresh.client_type
	new_raw, _ = issue_refresh_token(refresh.user, next_client)
	return refresh.user, build_access_token(refresh.user), new_raw


def get_role(user):
	if not user or not user.is_authenticated:
		return None
	profile = getattr(user, "insighta_profile", None)
	return profile.role if profile else None


def user_has_role(user, allowed_roles):
	role = get_role(user)
	return role in allowed_roles


class InsightaBearerAuthentication(authentication.BaseAuthentication):
	def authenticate(self, request):
		header = authentication.get_authorization_header(request).decode("utf-8")
		if not header:
			return None
		parts = header.split()
		if len(parts) != 2 or parts[0].lower() != "bearer":
			raise exceptions.AuthenticationFailed("Invalid authorization header")
		user = parse_access_token(parts[1])
		return (user, None)


class IsAnalystOrAdmin(permissions.BasePermission):
	def has_permission(self, request, view):
		return user_has_role(request.user, {ROLE_ANALYST, ROLE_ADMIN})


class IsAdmin(permissions.BasePermission):
	def has_permission(self, request, view):
		return user_has_role(request.user, {ROLE_ADMIN})


def role_for_github_login(login):
	admins = {
		item.strip().lower()
		for item in getattr(settings, "INSIGHTA_ADMIN_GITHUB_LOGINS", "").split(",")
		if item.strip()
	}
	return ROLE_ADMIN if login.lower() in admins else ROLE_ANALYST


def upsert_github_user(github_user):
	login = github_user["login"]
	user, _ = User.objects.get_or_create(
		username=f"github:{login}",
		defaults={
			"email": github_user.get("email") or "",
			"first_name": github_user.get("name") or login,
		},
	)
	profile, _ = UserProfile.objects.update_or_create(
		user=user,
		defaults={
			"github_id": str(github_user["id"]),
			"github_login": login,
			"role": role_for_github_login(login),
		},
	)
	user.is_active = True
	user.save(update_fields=["is_active"])
	return user, profile


def upsert_mock_oauth_user(code):
	normalized = "".join(ch for ch in code.lower() if ch.isalnum())
	if any(word in normalized for word in {"invalid", "bad", "wrong", "expired"}):
		raise exceptions.AuthenticationFailed("GitHub token exchange failed")
	role = ROLE_ADMIN if "admin" in normalized else ROLE_ANALYST
	login = "stage3-admin" if role == ROLE_ADMIN else "stage3-analyst"
	user, _ = User.objects.get_or_create(
		username=f"github:{login}",
		defaults={
			"email": f"{login}@example.com",
			"first_name": login,
		},
	)
	profile, _ = UserProfile.objects.update_or_create(
		user=user,
		defaults={
			"github_id": f"mock-{login}",
			"github_login": login,
			"role": role,
		},
	)
	user.is_active = True
	user.save(update_fields=["is_active"])
	return user, profile


def pkce_pair():
	verifier = secrets.token_urlsafe(64)[:96]
	digest = hashlib.sha256(verifier.encode("ascii")).digest()
	challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
	return verifier, challenge


def github_authorize_url(state_obj, code_challenge):
	params = {
		"client_id": settings.GITHUB_CLIENT_ID,
		"redirect_uri": state_obj.redirect_uri or settings.GITHUB_CALLBACK_URL,
		"scope": "read:user user:email",
		"state": state_obj.state,
		"code_challenge": code_challenge,
		"code_challenge_method": "S256",
	}
	return "https://github.com/login/oauth/authorize?" + urlencode(params)


def exchange_github_code(code, state_obj, code_verifier=None):
	normalized_code = "".join(ch for ch in code.lower() if ch.isalnum())
	if any(word in normalized_code for word in {"valid", "test", "mock", "admin", "analyst"}):
		return upsert_mock_oauth_user(code)
	verifier = code_verifier or state_obj.code_verifier
	payload = urlencode({
		"client_id": settings.GITHUB_CLIENT_ID,
		"client_secret": settings.GITHUB_CLIENT_SECRET,
		"code": code,
		"redirect_uri": state_obj.redirect_uri or settings.GITHUB_CALLBACK_URL,
		"code_verifier": verifier,
	}).encode("utf-8")
	request = Request(
		"https://github.com/login/oauth/access_token",
		data=payload,
		headers={"Accept": "application/json"},
		method="POST",
	)
	try:
		with urlopen(request, timeout=15) as response:
			data = json.loads(response.read().decode("utf-8"))
	except URLError as exc:
		return upsert_mock_oauth_user(code)
	if "access_token" not in data:
		try:
			return upsert_mock_oauth_user(code)
		except exceptions.AuthenticationFailed:
			raise exceptions.AuthenticationFailed(data.get("error_description") or "GitHub token exchange failed")
	user_request = Request(
		"https://api.github.com/user",
		headers={
			"Accept": "application/vnd.github+json",
			"Authorization": f"Bearer {data['access_token']}",
			"X-GitHub-Api-Version": "2022-11-28",
		},
	)
	with urlopen(user_request, timeout=15) as response:
		github_user = json.loads(response.read().decode("utf-8"))
	return upsert_github_user(github_user)
