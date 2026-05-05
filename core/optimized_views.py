# core/optimized_views.py

import math
from rest_framework import status
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.db.models import Q
from .models import Profile, ROLE_ADMIN, ROLE_ANALYST
from .auth_utils import user_has_role
from .cache_manager import QueryNormalizer, CacheManager
from .csv_ingestion import CSVChunkProcessor

# Import existing utilities (these stay the same)
from .views import (
    error_response, with_cors, RoleProtectedAPIView,
    profile_dict, get_page_params, paginated_response,
    VALID_SORT_FIELDS, VALID_ORDER
)


class OptimizedProfileListView(RoleProtectedAPIView):
    """
    Enhanced ProfileListView with:
    - Query result caching
    - Query normalization
    - Optimized database queries
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        """Get profiles with caching."""
        try:
            # Extract and normalize query parameters
            params = dict(request.query_params)
            page, limit = get_page_params(request.query_params)
            params['page'] = page
            params['limit'] = limit
            
            # Normalize filters for consistent cache keys
            normalized_params = QueryNormalizer.normalize_filters(params)
            
            # Check cache first
            cache_key = QueryNormalizer.get_cache_key(normalized_params, 'list')
            cached_result = CacheManager.get_query_result(normalized_params, 'list')
            
            if cached_result:
                return paginated_response(
                    request,
                    cached_result['data'],
                    page,
                    limit,
                    cached_result['total']
                )
            
            # Build query (database optimized)
            queryset = self._get_optimized_queryset(request.query_params)
            
            # Count total (this query is optimized with indexes)
            total = queryset.count()
            
            # Paginate
            offset = (page - 1) * limit
            if offset >= total and total > 0:
                return error_response("Page overlap detected", 400)
            
            # Use select_related/prefetch_related if needed (not applicable here, but good practice)
            # Fetch only needed data
            result_page = list(queryset[offset:offset + limit])
            
            # Cache the result
            CacheManager.set_query_result(
                normalized_params,
                {'data': result_page, 'total': total},
                ttl=CacheManager.LIST_CACHE_TTL,
                prefix='list'
            )
            
            return paginated_response(request, result_page, page, limit, total)
        
        except ValueError:
            return error_response("Invalid query parameters", 422)

    def post(self, request):
        """Create profile (unchanged)."""
        if not user_has_role(request.user, {ROLE_ADMIN}):
            return error_response("Admin role required", 403)
        
        from django.db import IntegrityError
        from django.utils import timezone
        
        name = request.data.get("name") or request.data.get("username") or f"profile-{timezone.now().timestamp()}"
        try:
            profile = Profile.objects.create(
                name=name,
                gender=request.data.get("gender", "unknown"),
                gender_probability=float(request.data.get("gender_probability", 0)),
                age=int(request.data.get("age", 0)),
                age_group=request.data.get("age_group", "unknown"),
                country_id=request.data.get("country_id", "NA")[:2],
                country_name=request.data.get("country_name", "Unknown"),
                country_probability=float(request.data.get("country_probability", 0)),
            )
        except IntegrityError:
            return error_response("Profile already exists", 409)
        except (TypeError, ValueError):
            return error_response("Invalid profile payload", 422)
        
        # Invalidate cache on write
        CacheManager.invalidate_profile_queries()
        
        return with_cors(Response({"status": "success", "data": profile_dict(profile)}, status=201))

    def delete(self, request):
        """Delete profile (unchanged)."""
        if not user_has_role(request.user, {ROLE_ADMIN}):
            return error_response("Admin role required", 403)
        
        profile_id = request.data.get("id") or request.data.get("profile_id") or request.query_params.get("id")
        if not profile_id:
            return error_response("Profile id is required", 400)
        
        deleted, _ = Profile.objects.filter(id=profile_id).delete()
        if not deleted:
            return error_response("Profile not found", 404)
        
        # Invalidate cache on delete
        CacheManager.invalidate_profile_queries()
        
        return with_cors(Response({"status": "success"}, status=200))

    @staticmethod
    def _get_optimized_queryset(params):
        """Build optimized queryset with proper indexing."""
        filters = Q()
        
        # Apply filters in order of selectivity (most selective first)
        gender = params.get("gender")
        if gender:
            filters &= Q(gender=gender)
        
        country_id = params.get("country_id")
        if country_id:
            filters &= Q(country_id=country_id)
        
        age_group = params.get("age_group")
        if age_group:
            filters &= Q(age_group=age_group)
        
        # Range queries (use indexes)
        try:
            min_age = int(params.get("min_age")) if params.get("min_age") else None
            max_age = int(params.get("max_age")) if params.get("max_age") else None
            min_gender_probability = float(params.get("min_gender_probability")) if params.get("min_gender_probability") else None
            min_country_probability = float(params.get("min_country_probability")) if params.get("min_country_probability") else None
        except ValueError:
            raise ValueError("Invalid query parameters")
        
        if min_age is not None:
            filters &= Q(age__gte=min_age)
        if max_age is not None:
            filters &= Q(age__lte=max_age)
        if min_gender_probability is not None:
            filters &= Q(gender_probability__gte=min_gender_probability)
        if min_country_probability is not None:
            filters &= Q(country_probability__gte=min_country_probability)
        
        # Build queryset with indexes
        queryset = Profile.objects.filter(filters)
        
        # Sorting (leverages indexes)
        sort_by = params.get("sort_by", "created_at")
        order = params.get("order", "desc")
        if sort_by not in VALID_SORT_FIELDS or order not in VALID_ORDER:
            raise ValueError("Invalid query parameters")
        
        ordering = ("-" if order == "desc" else "") + sort_by
        return queryset.order_by(ordering)


class OptimizedProfileSearchView(RoleProtectedAPIView):
    """
    Enhanced search with caching.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        """Search with normalized query caching."""
        from .views import parse_natural_language_query, profiles_for_search
        
        q = request.query_params.get("q", "").strip()
        if not q:
            return error_response("Missing or empty parameter", 400)
        
        # Normalize search query for caching
        search_params = {'q': q, 'page': request.query_params.get('page', 1), 'limit': request.query_params.get('limit', 10)}
        
        # Check cache
        cached_result = CacheManager.get_query_result(search_params, 'search')
        if cached_result:
            try:
                page, limit = get_page_params(request.query_params)
                return paginated_response(request, cached_result['data'], page, limit, cached_result['total'])
            except ValueError:
                return error_response("Invalid query parameters", 422)
        
        # Execute search
        queryset = profiles_for_search(q)
        if queryset is None:
            return error_response("Unable to interpret query", 400)
        
        try:
            page, limit = get_page_params(request.query_params)
        except ValueError:
            return error_response("Invalid query parameters", 422)
        
        total = queryset.count()
        offset = (page - 1) * limit
        if offset >= total and total > 0:
            return error_response("Page overlap detected", 400)
        
        result_page = list(queryset[offset:offset + limit])
        
        # Cache search result
        CacheManager.set_query_result(
            search_params,
            {'data': result_page, 'total': total},
            ttl=CacheManager.SEARCH_CACHE_TTL,
            prefix='search'
        )
        
        return paginated_response(request, result_page, page, limit, total)


class CSVUploadView(RoleProtectedAPIView):
    """
    New endpoint for CSV data ingestion.
    POST /api/v1/profiles/csv/upload
    
    Accepts multipart/form-data with 'file' field containing CSV.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        """Handle CSV file upload."""
        if not user_has_role(request.user, {ROLE_ADMIN}):
            return error_response("Admin role required", 403)
        
        if 'file' not in request.FILES:
            return error_response("No file provided", 400)
        
        file_obj = request.FILES['file']
        
        # Validate file size (max 50MB)
        if file_obj.size > 50 * 1024 * 1024:
            return error_response("File too large (max 50MB)", 413)
        
        try:
            # Process CSV with streaming
            result = CSVChunkProcessor.process_csv_file(file_obj)
            
            return with_cors(Response({
                "status": result.get('status', 'success'),
                "total_rows": result['total_rows'],
                "inserted": result['inserted'],
                "skipped": result['skipped'],
                "reasons": result['reasons'],
            }, status=201))
        
        except Exception as e:
            return error_response(f"CSV processing failed: {str(e)}", 422)