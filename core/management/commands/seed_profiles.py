from django.core.management.base import BaseCommand
from core.models import Profile
import json
from django.utils import timezone
from uuid6 import uuid7

def generate_uuid():
	return uuid7()
class Command(BaseCommand):
    help = 'Seed the database with profiles from a JSON file.'

    def add_arguments(self, parser):
        parser.add_argument('--json_path', type=str, default=None, help='Path to the profiles JSON file (default: data/profiles.json)')

    def handle(self, *args, **options):
        json_path = options.get('json_path') or 'data/seed_profiles.json'
        try:
            with open(json_path, 'r', encoding='utf-8') as f:
                profiles = json.load(f)["profiles"]
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Failed to load profiles JSON: {e}"))
            return
        created, skipped = 0, 0
        for p in profiles:
            created_at = p.get('created_at')
            if created_at:
                # Ensure UTC ISO 8601
                from django.utils.dateparse import parse_datetime
                created_at = parse_datetime(created_at)
                if created_at is None:
                    created_at = timezone.now()
            else:
                created_at = timezone.now()
            obj, created_flag = Profile.objects.get_or_create(
                name=p['name'],
                defaults={
                    'id': generate_uuid(),
                    'gender': p['gender'],
                    'gender_probability': p['gender_probability'],
                    'age': p['age'],
                    'age_group': p['age_group'],
                    'country_id': p['country_id'],
                    'country_name': p['country_name'],
                    'country_probability': p['country_probability'],
                    'created_at': created_at,
                }
            )
            if created_flag:
                created += 1
            else:
                skipped += 1
        self.stdout.write(self.style.SUCCESS(f"Seeded: {created}, Skipped (duplicates): {skipped}"))
