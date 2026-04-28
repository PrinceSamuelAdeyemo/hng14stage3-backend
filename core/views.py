
from rest_framework.views import APIView
from rest_framework.response import Response
from django.db.models import Q
from .models import Profile
from .serializers import ProfileSerializer
import re

def parse_natural_language_query(q):
	q = q.lower().strip()
	filters = {}
	# Gender
	if "male" in q and "female" in q:
		filters["gender"] = None  # Both genders, so don't filter by gender
	elif "male" in q:
		filters["gender"] = "male"
	elif "female" in q:
		filters["gender"] = "female"
	# Age group
	if "child" in q:
		filters["age_group"] = "child"
	elif "teenager" in q or "teenagers" in q:
		filters["age_group"] = "teenager"
	elif "adult" in q:
		filters["age_group"] = "adult"
	elif "senior" in q:
		filters["age_group"] = "senior"
	# Young (special mapping)
	if "young" in q:
		filters["min_age"] = 16
		filters["max_age"] = 24
	# Above/below/over/under
	m = re.search(r"(above|over) (\d+)", q)
	if m:
		filters["min_age"] = int(m.group(2))
	m = re.search(r"(below|under) (\d+)", q)
	if m:
		filters["max_age"] = int(m.group(2))
	# From country
	m = re.search(r"from ([a-z ]+)", q)
	if m:
		country = m.group(1).strip()
		# Map country name to country_id (ISO2) for common cases
		country_map = {
			"nigeria": "NG", "angola": "AO", "kenya": "KE", "benin": "BJ"
		}
		if country in country_map:
			filters["country_id"] = country_map[country]
		else:
			filters["country_name"] = country.title()
	# Edge: "teenagers above 17"
	m = re.search(r"teenagers? (above|over) (\d+)", q)
	if m:
		filters["age_group"] = "teenager"
		filters["min_age"] = int(m.group(2))
	# If nothing interpretable, return None
	if not filters:
		return None
	return filters

class ProfileSearchView(APIView):
	def get(self, request):
		q = request.query_params.get("q", "").strip()
		if not q:
			return self._error("Missing or empty parameter", 400)
		filters = parse_natural_language_query(q)
		if filters is None:
			return self._error("Unable to interpret query", 400)
		q_obj = Q()
		if filters.get("gender"):
			q_obj &= Q(gender=filters["gender"])
		if filters.get("age_group"):
			q_obj &= Q(age_group=filters["age_group"])
		if filters.get("country_id"):
			q_obj &= Q(country_id=filters["country_id"])
		if filters.get("country_name"):
			q_obj &= Q(country_name__iexact=filters["country_name"])
		if filters.get("min_age"):
			q_obj &= Q(age__gte=filters["min_age"])
		if filters.get("max_age"):
			q_obj &= Q(age__lte=filters["max_age"])
		# Pagination
		try:
			page = int(request.query_params.get("page", 1))
			limit = int(request.query_params.get("limit", 10))
			if limit > 50:
				limit = 50
		except ValueError:
			return self._error("Invalid query parameters", 422)
		queryset = Profile.objects.filter(q_obj)
		total = queryset.count()
		offset = (page - 1) * limit
		if offset >= total and total > 0:
			return self._error("Page overlap detected or insufficient records", 400)
		result_page = queryset[offset:offset+limit]
		serializer = ProfileSerializer(result_page, many=True)
		resp = Response({
			"status": "success",
			"page": page,
			"limit": limit,
			"total": total,
			"data": [self._format_profile(p) for p in result_page]
		}, status=200)
		resp["Access-Control-Allow-Origin"] = "*"
		return resp

	def _error(self, msg, code):
		resp = Response({"status": "error", "message": msg}, status=code)
		resp["Access-Control-Allow-Origin"] = "*"
		return resp

	def _format_profile(self, p):
		return {
			"id": str(p.id),
			"name": p.name,
			"gender": p.gender,
			"gender_probability": float(p.gender_probability),
			"age": int(p.age),
			"age_group": p.age_group,
			"country_id": p.country_id,
			"country_name": p.country_name,
			"country_probability": float(p.country_probability),
			"created_at": p.created_at.replace(tzinfo=None).isoformat() + "Z" if p.created_at else None
		}
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.pagination import PageNumberPagination
from django.db.models import Q
from .models import Profile
from .serializers import ProfileSerializer

VALID_SORT_FIELDS = {"age", "created_at", "gender_probability"}
VALID_ORDER = {"asc", "desc"}

class ProfileListView(APIView):
	def get(self, request):
		params = request.query_params
		filters = Q()
		# Filtering
		gender = params.get("gender")
		if gender:
			filters &= Q(gender=gender)
		age_group = params.get("age_group")
		if age_group:
			filters &= Q(age_group=age_group)
		country_id = params.get("country_id")
		if country_id:
			filters &= Q(country_id=country_id)
		try:
			min_age = int(params.get("min_age")) if params.get("min_age") else None
			max_age = int(params.get("max_age")) if params.get("max_age") else None
			min_gender_probability = float(params.get("min_gender_probability")) if params.get("min_gender_probability") else None
			min_country_probability = float(params.get("min_country_probability")) if params.get("min_country_probability") else None
		except ValueError:
			return self._error("Invalid query parameters", 422)
		if min_age is not None:
			filters &= Q(age__gte=min_age)
		if max_age is not None:
			filters &= Q(age__lte=max_age)
		if min_gender_probability is not None:
			filters &= Q(gender_probability__gte=min_gender_probability)
		if min_country_probability is not None:
			filters &= Q(country_probability__gte=min_country_probability)
		# Sorting
		sort_by = params.get("sort_by", "created_at")
		order = params.get("order", "desc")
		if sort_by not in VALID_SORT_FIELDS or order not in VALID_ORDER:
			return self._error("Invalid query parameters", 422)
		ordering = ("-" if order == "desc" else "") + sort_by
		# Pagination
		try:
			page = int(params.get("page", 1))
			limit = int(params.get("limit", 10))
			if limit > 50:
				limit = 50
		except ValueError:
			return self._error("Invalid query parameters", 422)
		queryset = Profile.objects.filter(filters).order_by(ordering)
		total = queryset.count()
		offset = (page - 1) * limit
		if offset >= total and total > 0:
			return self._error("Page overlap detected or insufficient records", 400)
		result_page = queryset[offset:offset+limit]
		resp = Response({
			"status": "success",
			"page": page,
			"limit": limit,
			"total": total,
			"data": [self._format_profile(p) for p in result_page]
		}, status=200)
		resp["Access-Control-Allow-Origin"] = "*"
		return resp

	def _error(self, msg, code):
		resp = Response({"status": "error", "message": msg}, status=code)
		resp["Access-Control-Allow-Origin"] = "*"
		return resp

	def _format_profile(self, p):
		return {
			"id": str(p.id),
			"name": p.name,
			"gender": p.gender,
			"gender_probability": float(p.gender_probability),
			"age": int(p.age),
			"age_group": p.age_group,
			"country_id": p.country_id,
			"country_name": p.country_name,
			"country_probability": float(p.country_probability),
			"created_at": p.created_at.replace(tzinfo=None).isoformat() + "Z" if p.created_at else None
		}
