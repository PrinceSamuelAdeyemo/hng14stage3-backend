# core/cache_manager.py

import json
import hashlib
from typing import Dict, Any, Optional
from django.core.cache import cache
from django.db.models import Q

class QueryNormalizer:
    """
    Normalizes query filters into a canonical form.
    Ensures "females aged 20-45 from Nigeria" produces the same cache key
    as "Nigerian females between 20-45".
    """
    
    # Canonical order of filter keys
    CANONICAL_ORDER = [
        'gender',
        'age_group',
        'min_age',
        'max_age',
        'country_id',
        'country_name',
        'min_gender_probability',
        'min_country_probability',
        'sort_by',
        'order',
        'page',
        'limit',
    ]
    
    @staticmethod
    def normalize_filters(filters: Dict[str, Any]) -> Dict[str, Any]:
        """
        Normalize filter object into canonical form.
        - Remove None/empty values
        - Convert types consistently
        - Sort keys in canonical order
        """
        normalized = {}
        
        # Gender normalization
        if filters.get('gender'):
            normalized['gender'] = filters['gender'].lower().strip()
        
        # Age group normalization
        if filters.get('age_group'):
            normalized['age_group'] = filters['age_group'].lower().strip()
        
        # Age range normalization - ensure integers
        if filters.get('min_age'):
            try:
                normalized['min_age'] = int(filters['min_age'])
            except (ValueError, TypeError):
                pass
        
        if filters.get('max_age'):
            try:
                normalized['max_age'] = int(filters['max_age'])
            except (ValueError, TypeError):
                pass
        
        # Country normalization
        if filters.get('country_id'):
            normalized['country_id'] = filters['country_id'].upper().strip()[:2]
        
        if filters.get('country_name'):
            normalized['country_name'] = filters['country_name'].title().strip()
        
        # Probability thresholds normalization
        if filters.get('min_gender_probability'):
            try:
                normalized['min_gender_probability'] = round(float(filters['min_gender_probability']), 3)
            except (ValueError, TypeError):
                pass
        
        if filters.get('min_country_probability'):
            try:
                normalized['min_country_probability'] = round(float(filters['min_country_probability']), 3)
            except (ValueError, TypeError):
                pass
        
        # Sort and pagination parameters
        if filters.get('sort_by'):
            normalized['sort_by'] = filters['sort_by'].lower().strip()
        
        if filters.get('order'):
            order = filters['order'].lower().strip()
            if order in ('asc', 'desc'):
                normalized['order'] = order
        
        if filters.get('page'):
            try:
                normalized['page'] = int(filters['page'])
            except (ValueError, TypeError):
                pass
        
        if filters.get('limit'):
            try:
                normalized['limit'] = int(filters['limit'])
            except (ValueError, TypeError):
                pass
        
        return normalized
    
    @staticmethod
    def get_cache_key(filters: Dict[str, Any], prefix: str = 'query') -> str:
        """
        Generate a deterministic cache key from normalized filters.
        
        Args:
            filters: Filter dictionary (will be normalized)
            prefix: Cache key prefix
        
        Returns:
            Hash-based cache key
        """
        normalized = QueryNormalizer.normalize_filters(filters)
        
        # Sort by canonical order, only include keys that exist
        ordered = {}
        for key in QueryNormalizer.CANONICAL_ORDER:
            if key in normalized:
                ordered[key] = normalized[key]
        
        # Create deterministic JSON representation
        json_str = json.dumps(ordered, sort_keys=True, separators=(',', ':'))
        
        # Hash to create short, consistent key
        hash_digest = hashlib.md5(json_str.encode()).hexdigest()
        
        return f"{prefix}:{hash_digest}"


class CacheManager:
    """
    Manages caching of query results with TTL and cache invalidation.
    """
    
    DEFAULT_CACHE_TTL = 300  # 5 minutes
    LIST_CACHE_TTL = 600     # 10 minutes
    SEARCH_CACHE_TTL = 300   # 5 minutes
    
    @staticmethod
    def get_query_result(filters: Dict[str, Any], prefix: str = 'query') -> Optional[Any]:
        """
        Retrieve cached query result if exists.
        
        Args:
            filters: Filter dictionary
            prefix: Cache key prefix
        
        Returns:
            Cached result or None
        """
        cache_key = QueryNormalizer.get_cache_key(filters, prefix)
        return cache.get(cache_key)
    
    @staticmethod
    def set_query_result(filters: Dict[str, Any], data: Any, ttl: int = None, prefix: str = 'query'):
        """
        Cache a query result with TTL.
        
        Args:
            filters: Filter dictionary
            data: Result to cache
            ttl: Time to live in seconds (uses default if None)
            prefix: Cache key prefix
        """
        if ttl is None:
            ttl = CacheManager.DEFAULT_CACHE_TTL
        
        cache_key = QueryNormalizer.get_cache_key(filters, prefix)
        cache.set(cache_key, data, ttl)
    
    @staticmethod
    def invalidate_all():
        """Invalidate all Insighta-related cache."""
        # In production, use cache.clear() carefully or implement pattern-based invalidation
        cache.clear()
    
    @staticmethod
    def invalidate_profile_queries():
        """Invalidate profile query cache (called after data changes)."""
        # For simplicity, clear all cache. In production, use Redis KEYS pattern
        cache.clear()