from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase
from rest_framework.test import APIClient

from .auth_utils import build_access_token, issue_refresh_token
from .models import OAuthState, Profile, ROLE_ADMIN, ROLE_ANALYST, UserProfile


class StageThreeApiTests(TestCase):
	def setUp(self):
		cache.clear()
		self.user = User.objects.create_user(username="github:octo")
		UserProfile.objects.create(user=self.user, github_login="octo", github_id="1", role=ROLE_ANALYST)
		self.token = build_access_token(self.user)
		self.client = APIClient()
		Profile.objects.create(
			name="Ada",
			gender="female",
			gender_probability=0.98,
			age=32,
			age_group="adult",
			country_id="NG",
			country_name="Nigeria",
			country_probability=0.91,
		)

	def auth(self):
		self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.token}")

	def test_profiles_require_authentication(self):
		response = self.client.get("/api/v1/profiles")
		self.assertEqual(response.status_code, 403)

	def test_v1_profiles_use_updated_pagination_shape(self):
		self.auth()
		response = self.client.get("/api/v1/profiles")
		self.assertEqual(response.status_code, 200)
		self.assertIn("pagination", response.data)
		self.assertEqual(response.data["pagination"]["total"], 1)
		self.assertEqual(response.data["data"][0]["name"], "Ada")

	def test_legacy_api_shape_is_preserved(self):
		self.auth()
		response = self.client.get("/api/profiles")
		self.assertEqual(response.status_code, 200)
		self.assertIn("page", response.data)
		self.assertNotIn("pagination", response.data)

	def test_natural_language_search_handles_females(self):
		self.auth()
		response = self.client.get("/api/v1/profiles/search", {"q": "females above 30"})
		self.assertEqual(response.status_code, 200)
		self.assertEqual(response.data["pagination"]["total"], 1)

	def test_refresh_token_rotates(self):
		refresh, _ = issue_refresh_token(self.user, "cli")
		response = self.client.post("/api/v1/auth/refresh", {"refresh_token": refresh}, format="json")
		self.assertEqual(response.status_code, 200)
		self.assertIn("access_token", response.data)
		self.assertNotEqual(refresh, response.data["refresh_token"])

	def test_github_start_alias_redirects_with_pkce_and_cors(self):
		response = self.client.get("/auth/github")
		self.assertEqual(response.status_code, 302)
		self.assertIn("https://github.com/login/oauth/authorize", response["Location"])
		self.assertIn("code_challenge=", response["Location"])
		self.assertIn("state=", response["Location"])
		self.assertEqual(response["Access-Control-Allow-Origin"], "*")

	def test_github_cli_callback_validates_pkce_and_returns_tokens(self):
		start = self.client.get("/auth/github", {"client": "cli"})
		state = start.data["state"]
		verifier = start.data["code_verifier"]
		bad = self.client.get("/auth/github/callback", {
			"code": "valid-code",
			"state": state,
			"code_verifier": "wrong",
		})
		self.assertEqual(bad.status_code, 400)
		response = self.client.get("/auth/github/callback", {
			"code": "analyst-code",
			"state": state,
			"code_verifier": verifier,
		})
		self.assertEqual(response.status_code, 200)
		self.assertIn("access_token", response.data)
		self.assertIn("refresh_token", response.data)
		self.assertEqual(response.data["role"], ROLE_ANALYST)

	def test_github_cli_callback_can_issue_admin_token(self):
		start = self.client.get("/auth/github", {"client": "cli"})
		response = self.client.get("/auth/github/callback", {
			"code": "admin-code",
			"state": start.data["state"],
			"code_verifier": start.data["code_verifier"],
		})
		self.assertEqual(response.status_code, 200)
		self.assertEqual(response.data["role"], ROLE_ADMIN)
		self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {response.data['access_token']}")
		me = self.client.get("/api/users/me")
		self.assertEqual(me.status_code, 200)
		self.assertEqual(me.data["user"]["role"], ROLE_ADMIN)

	def test_github_callback_rejects_missing_and_invalid_values(self):
		self.assertEqual(self.client.get("/auth/github/callback").status_code, 400)
		state = OAuthState.objects.create(code_verifier="verifier", client_type="cli")
		response = self.client.get("/auth/github/callback", {
			"code": "invalid-code",
			"state": state.state,
			"code_verifier": "verifier",
		})
		self.assertEqual(response.status_code, 401)

	def test_auth_github_rate_limit_is_ten_requests_per_minute(self):
		for _ in range(10):
			self.assertEqual(self.client.get("/auth/github").status_code, 302)
		response = self.client.get("/auth/github")
		self.assertEqual(response.status_code, 429)
