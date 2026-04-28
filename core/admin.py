from django.contrib import admin
from .models import Profile

@admin.register(Profile)
class ProfileAdmin(admin.ModelAdmin):
	list_display = ("id", "name", "gender", "gender_probability", "age", "age_group", "country_id", "country_name", "country_probability", "created_at")
	search_fields = ("name", "country_name", "country_id")
	list_filter = ("gender", "age_group", "country_id")
