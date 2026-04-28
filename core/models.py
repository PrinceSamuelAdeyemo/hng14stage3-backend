from django.db import models
import uuid
from django.utils import timezone

def generate_uuid():
	"""Generate a UUID"""
	return uuid.uuid7

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
