from django.contrib import admin
from .models import Profile, RefreshToken, RequestLog, UserProfile

@admin.register(Profile)
class ProfileAdmin(admin.ModelAdmin):
	list_display = ("id", "name", "gender", "gender_probability", "age", "age_group", "country_id", "country_name", "country_probability", "created_at")
	search_fields = ("name", "country_name", "country_id")
	list_filter = ("gender", "age_group", "country_id")

@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
	list_display = ("user", "github_login", "role", "created_at", "updated_at")
	search_fields = ("github_login", "github_id")
	list_filter = ("role",)

@admin.register(RefreshToken)
class RefreshTokenAdmin(admin.ModelAdmin):
	list_display = ("user", "client_type", "created_at", "expires_at", "revoked_at")
	list_filter = ("client_type", "revoked_at")
	search_fields = ("user__username",)

@admin.register(RequestLog)
class RequestLogAdmin(admin.ModelAdmin):
	list_display = ("method", "path", "status_code", "user", "ip_address", "duration_ms", "created_at")
	list_filter = ("method", "status_code")
	search_fields = ("path", "user__username", "ip_address")
