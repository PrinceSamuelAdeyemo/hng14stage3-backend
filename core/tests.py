from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APIClient

from .auth_utils import build_access_token, issue_refresh_token
from .models import Profile, ROLE_ANALYST, UserProfile


class StageThreeApiTests(TestCase):
	def setUp(self):
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
