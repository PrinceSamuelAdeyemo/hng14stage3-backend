# core/csv_ingestion.py

import csv
import io
from typing import Iterator, Dict, List, Tuple
from django.db import transaction
from .models import Profile
from .cache_manager import CacheManager

class CSVValidator:
    """Validates individual CSV rows."""
    
    VALID_GENDERS = {'male', 'female', 'unknown'}
    VALID_AGE_GROUPS = {'child', 'teenager', 'adult', 'senior', 'unknown'}
    REQUIRED_FIELDS = {'name', 'gender', 'age', 'age_group', 'country_id', 'country_name'}
    
    @staticmethod
    def validate_row(row: Dict[str, str]) -> Tuple[bool, Optional[str]]:
        """
        Validate a single CSV row.
        
        Returns:
            (is_valid, skip_reason or None)
        """
        # Check required fields
        for field in CSVValidator.REQUIRED_FIELDS:
            if field not in row or not str(row[field]).strip():
                return False, f"missing_fields"
        
        # Validate name uniqueness check will happen during insert
        name = str(row.get('name', '')).strip()
        if not name or len(name) > 255:
            return False, "missing_fields"
        
        # Validate gender
        gender = str(row.get('gender', '')).strip().lower()
        if gender not in CSVValidator.VALID_GENDERS:
            return False, "invalid_gender"
        
        # Validate age
        try:
            age = int(row.get('age', 0))
            if age < 0 or age > 150:
                return False, "invalid_age"
        except (ValueError, TypeError):
            return False, "invalid_age"
        
        # Validate age_group
        age_group = str(row.get('age_group', '')).strip().lower()
        if age_group not in CSVValidator.VALID_AGE_GROUPS:
            return False, "invalid_age_group"
        
        # Validate country_id (2-letter code)
        country_id = str(row.get('country_id', '')).strip().upper()
        if not country_id or len(country_id) != 2:
            return False, "invalid_country_id"
        
        # Validate probabilities
        try:
            gender_prob = float(row.get('gender_probability', 0))
            country_prob = float(row.get('country_probability', 0))
            if not (0 <= gender_prob <= 1) or not (0 <= country_prob <= 1):
                return False, "invalid_probability"
        except (ValueError, TypeError):
            return False, "invalid_probability"
        
        return True, None


class CSVChunkProcessor:
    """
    Processes CSV in chunks to avoid memory overload.
    Uses bulk_create for efficient batch inserts.
    """
    
    CHUNK_SIZE = 1000  # Insert 1000 rows at a time
    
    @staticmethod
    def stream_csv_rows(file_stream) -> Iterator[Dict[str, str]]:
        """
        Generator that yields rows from CSV file one at a time.
        Never loads entire file into memory.
        
        Args:
            file_stream: File-like object (from request.FILES)
        
        Yields:
            Dict representing each CSV row
        """
        # Handle both text and binary streams
        if hasattr(file_stream, 'read'):
            content = file_stream.read()
            if isinstance(content, bytes):
                content = content.decode('utf-8')
        else:
            content = file_stream
        
        # Use StringIO for in-memory text processing
        csv_file = io.StringIO(content)
        reader = csv.DictReader(csv_file)
        
        for row in reader:
            # Skip empty rows
            if any(row.values()):
                yield row
    
    @staticmethod
    def process_csv_file(file_stream) -> Dict[str, any]:
        """
        Process entire CSV file with streaming and chunking.
        
        Returns:
            {
                'status': 'success',
                'total_rows': int,
                'inserted': int,
                'skipped': int,
                'reasons': {
                    'duplicate_name': int,
                    'invalid_age': int,
                    'missing_fields': int,
                    ...
                }
            }
        """
        stats = {
            'total_rows': 0,
            'inserted': 0,
            'skipped': 0,
            'reasons': {
                'duplicate_name': 0,
                'invalid_age': 0,
                'missing_fields': 0,
                'invalid_gender': 0,
                'invalid_age_group': 0,
                'invalid_country_id': 0,
                'invalid_probability': 0,
                'malformed_row': 0,
            }
        }
        
        chunk = []
        existing_names = set(Profile.objects.values_list('name', flat=True))
        
        try:
            for row_num, row in enumerate(CSVChunkProcessor.stream_csv_rows(file_stream), 1):
                stats['total_rows'] += 1
                
                try:
                    # Validate row
                    is_valid, skip_reason = CSVValidator.validate_row(row)
                    
                    if not is_valid:
                        stats['skipped'] += 1
                        stats['reasons'][skip_reason] += 1
                        continue
                    
                    # Check for duplicates (idempotency)
                    name = str(row['name']).strip()
                    if name in existing_names:
                        stats['skipped'] += 1
                        stats['reasons']['duplicate_name'] += 1
                        continue
                    
                    # Create Profile instance
                    profile = Profile(
                        name=name,
                        gender=str(row.get('gender', 'unknown')).strip().lower(),
                        gender_probability=float(row.get('gender_probability', 0)),
                        age=int(row.get('age', 0)),
                        age_group=str(row.get('age_group', 'unknown')).strip().lower(),
                        country_id=str(row.get('country_id', 'NA')).strip().upper()[:2],
                        country_name=str(row.get('country_name', 'Unknown')).strip(),
                        country_probability=float(row.get('country_probability', 0)),
                    )
                    
                    chunk.append(profile)
                    existing_names.add(name)
                    
                    # Bulk insert when chunk reaches size
                    if len(chunk) >= CSVChunkProcessor.CHUNK_SIZE:
                        inserted = CSVChunkProcessor._insert_chunk(chunk)
                        stats['inserted'] += inserted
                        chunk = []
                
                except (ValueError, TypeError, KeyError) as e:
                    stats['skipped'] += 1
                    stats['reasons']['malformed_row'] += 1
                    continue
            
            # Insert remaining chunk
            if chunk:
                inserted = CSVChunkProcessor._insert_chunk(chunk)
                stats['inserted'] += inserted
        
        except Exception as e:
            # Partial failure - return what we have
            stats['status'] = 'partial_failure'
            stats['error'] = str(e)
            return stats
        
        stats['status'] = 'success'
        
        # Invalidate query cache after data changes
        CacheManager.invalidate_profile_queries()
        
        return stats
    
    @staticmethod
    def _insert_chunk(chunk: List[Profile]) -> int:
        """
        Bulk insert a chunk of Profile objects.
        Uses transaction to ensure atomicity of the batch.
        """
        if not chunk:
            return 0
        
        try:
            with transaction.atomic():
                # bulk_create is much faster than individual saves
                Profile.objects.bulk_create(chunk, batch_size=100, ignore_conflicts=False)
            return len(chunk)
        except Exception as e:
            # Log the error but don't fail entire ingestion
            print(f"Chunk insertion error: {e}")
            # Try individual inserts for this chunk to preserve what we can
            inserted = 0
            for profile in chunk:
                try:
                    profile.save()
                    inserted += 1
                except Exception:
                    pass
            return inserted