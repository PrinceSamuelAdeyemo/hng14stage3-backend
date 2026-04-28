# Insighta Labs Demographic Intelligence Backend

## Overview
This backend provides advanced demographic profile querying for Insighta Labs clients. It supports:
- Filtering, sorting, and pagination of profiles
- Rule-based natural language search
- Fast, combinable queries for marketing, product, and analytics teams

## API Endpoints

### 1. Get All Profiles
**GET** `/api/profiles`

**Query Parameters:**
- `gender`: `male` or `female`
- `age_group`: `child`, `teenager`, `adult`, `senior`
- `country_id`: ISO2 code (e.g., `NG`, `BJ`)
- `min_age`, `max_age`: integer
- `min_gender_probability`, `min_country_probability`: float
- `sort_by`: `age`, `created_at`, `gender_probability`
- `order`: `asc` or `desc`
- `page`: integer (default: 1)
- `limit`: integer (default: 10, max: 50)

**Response:**
```
{
  "status": "success",
  "page": 1,
  "limit": 10,
  "total": 2026,
  "data": [ ... ]
}
```

### 2. Natural Language Query
**GET** `/api/profiles/search?q=...`

**Examples:**
- `q=young males from nigeria` → `gender=male`, `min_age=16`, `max_age=24`, `country_id=NG`
- `q=females above 30` → `gender=female`, `min_age=30`
- `q=people from angola` → `country_id=AO`
- `q=adult males from kenya` → `gender=male`, `age_group=adult`, `country_id=KE`
- `q=male and female teenagers above 17` → `age_group=teenager`, `min_age=17`

**Response:**
```
{
  "status": "success",
  "page": 1,
  "limit": 10,
  "total": 2026,
  "data": [ ... ]
}
```

**Error Response:**
```
{
  "status": "error",
  "message": "<error message>"
}
```

## Natural Language Parsing Approach
- **Rule-based only** (no AI/LLM)
- Recognizes keywords: `male`, `female`, `child`, `teenager`, `adult`, `senior`, `young`, `above`, `over`, `below`, `under`, `from <country>`
- Maps common country names to ISO2 codes (NG, AO, KE, BJ)
- "young" → ages 16–24
- "above 30"/"over 30" → min_age=30
- "below 20"/"under 20" → max_age=20
- "teenagers above 17" → age_group=teenager, min_age=17
- All filters are combinable
- If the query can't be interpreted, returns `{ "status": "error", "message": "Unable to interpret query" }`

## Limitations
- Only exact keywords and simple patterns are supported
- No fuzzy matching, synonyms, or advanced NLP
- Only a few countries are mapped by name; others use the country name as-is
- No support for ranges (e.g., "ages 20 to 30") or logical operators ("or", "not")
- Misspellings and ambiguous queries are not handled

## Seeding the Database
- Place your profiles JSON file at `data/profiles.json`
- Run: `python manage.py seed_profiles`
- Re-running the seed is safe (no duplicates)

## CORS
- All API responses include `Access-Control-Allow-Origin: *`

## Timestamps & IDs
- All timestamps are UTC ISO 8601
- All IDs are UUID v7

## Error Handling
- 400: Missing/empty parameter or page overlap
- 422: Invalid parameter type
- 404: Not found
- 500/502: Server error

---

For more, see the code and comments in `core/views.py` and `core/management/commands/seed_profiles.py`.
