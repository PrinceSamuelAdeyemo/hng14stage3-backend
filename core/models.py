from django.db import models
from django.conf import settings
from django.utils.crypto import get_random_string
from django.utils import timezone
from datetime import timedelta
from uuid6 import uuid7

def generate_uuid():
	return uuid7()

def generate_oauth_state():
	return get_random_string(64)

ROLE_ADMIN = "admin"
ROLE_ANALYST = "analyst"
ROLE_CHOICES = (
	(ROLE_ADMIN, "Admin"),
	(ROLE_ANALYST, "Analyst"),
)

class Profile(models.Model):
	id = models.UUIDField(primary_key=True, default=generate_uuid, editable=False)
	name = models.CharField(max_length=255, unique=True)
	gender = models.CharField(max_length=10)
	gender_probability = models.FloatField()
	age = models.IntegerField()
	age_group = models.CharField(max_length=10)
	country_id = models.CharField(max_length=2)
	country_name = models.CharField(max_length=255)
	country_probability = models.FloatField()
	created_at = models.DateTimeField(default=timezone.now, editable=False)

	class Meta:
		indexes = [
			models.Index(fields=["gender"]),
			models.Index(fields=["age_group"]),
			models.Index(fields=["country_id"]),
			models.Index(fields=["age"]),
		]
		ordering = ["-created_at"]

	def __str__(self):
		return self.name

class UserProfile(models.Model):
	user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="insighta_profile")
	github_id = models.CharField(max_length=64, unique=True, null=True, blank=True)
	github_login = models.CharField(max_length=255, unique=True)
	role = models.CharField(max_length=20, choices=ROLE_CHOICES, default=ROLE_ANALYST)
	created_at = models.DateTimeField(default=timezone.now)
	updated_at = models.DateTimeField(auto_now=True)

	def __str__(self):
		return f"{self.github_login} ({self.role})"

class OAuthState(models.Model):
	CLIENT_CHOICES = (
		("cli", "CLI"),
		("web", "Web"),
	)

	state = models.CharField(max_length=128, unique=True, default=generate_oauth_state)
	code_verifier = models.CharField(max_length=255)
	client_type = models.CharField(max_length=10, choices=CLIENT_CHOICES)
	redirect_uri = models.URLField(blank=True)
	next_url = models.CharField(max_length=512, blank=True)
	created_at = models.DateTimeField(default=timezone.now)
	used_at = models.DateTimeField(null=True, blank=True)

	def is_expired(self):
		return self.created_at < timezone.now() - timedelta(minutes=10)

	def mark_used(self):
		self.used_at = timezone.now()
		self.save(update_fields=["used_at"])

class RefreshToken(models.Model):
	user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="insighta_refresh_tokens")
	token_hash = models.CharField(max_length=128, unique=True)
	client_type = models.CharField(max_length=10, default="cli")
	created_at = models.DateTimeField(default=timezone.now)
	expires_at = models.DateTimeField()
	revoked_at = models.DateTimeField(null=True, blank=True)

	def is_active(self):
		return self.revoked_at is None and self.expires_at > timezone.now()

class RequestLog(models.Model):
	method = models.CharField(max_length=10)
	path = models.CharField(max_length=512)
	status_code = models.PositiveIntegerField()
	user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
	ip_address = models.GenericIPAddressField(null=True, blank=True)
	duration_ms = models.PositiveIntegerField(default=0)
	created_at = models.DateTimeField(default=timezone.now)

	class Meta:
		indexes = [
			models.Index(fields=["created_at"]),
			models.Index(fields=["path"]),
			models.Index(fields=["user"]),
		]
