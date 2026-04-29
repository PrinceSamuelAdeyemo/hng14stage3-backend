# Insighta Labs+ Profile Intelligence Backend

Insighta Labs+ is a Django/DRF profile intelligence platform. Stage 2 profile filtering, sorting, pagination, and natural language search remain available, and Stage 3 adds GitHub OAuth, short-lived tokens, role enforcement, CSV export, a CLI, a browser portal, rate limiting, and request logging.

## System Architecture

- `config/urls.py` exposes legacy `/api/...` routes and versioned `/api/v1/...` routes.
- `core/views.py` contains the profile API, GitHub OAuth endpoints, token refresh/logout endpoints, CSV export, and web portal views.
- `core/auth_utils.py` signs access tokens, rotates refresh tokens, performs GitHub OAuth code exchange with PKCE, and centralizes role checks.
- `core/models.py` stores profiles, GitHub user roles, OAuth state, hashed refresh tokens, and request logs.
- `core/middleware.py` applies API rate limiting and logs API requests.
- `insighta_cli.py` provides the globally installable CLI entrypoint.

## Authentication Flow

GitHub OAuth uses PKCE for both browser and CLI clients.

Browser:

1. Visit `/portal/`.
2. Click GitHub sign-in, which starts `/api/v1/auth/github/start?client=web`.
3. The backend creates an OAuth state, code verifier, and code challenge, then redirects to GitHub.
4. GitHub redirects to `/api/v1/auth/github/callback`.
5. The backend exchanges the code, creates or updates the Django user, assigns a role, starts a Django session, and sets an HTTP-only refresh cookie.

CLI:

1. `insighta login --api http://localhost:8000/api/v1`
2. The CLI starts `/api/v1/auth/github/start?client=cli&redirect_uri=http://127.0.0.1:<port>/callback`.
3. The CLI opens the GitHub authorization URL and listens locally for the callback.
4. The CLI sends the returned code, state, and PKCE verifier to `/api/v1/auth/github/callback`.
5. The backend returns access and refresh tokens, and the CLI stores them at `~/.insighta/credentials.json`.

Required environment variables:

```env
GITHUB_CLIENT_ID=...
GITHUB_CLIENT_SECRET=...
GITHUB_CALLBACK_URL=http://localhost:8000/api/v1/auth/github/callback
WEB_PORTAL_URL=https://backendstage3-webportal.vercel.app/
INSIGHTA_API=http://localhost:8000/api/v1
INSIGHTA_ADMIN_GITHUB_LOGINS=github_admin_login,another_admin
INSIGHTA_ACCESS_TOKEN_SECONDS=900
INSIGHTA_REFRESH_TOKEN_DAYS=7
```

## Token Handling Approach

- Access tokens are signed Django tokens with a short default lifetime of 15 minutes.
- Refresh tokens are random secrets stored only as SHA-256 hashes in the database.
- Refresh tokens rotate on every `/api/v1/auth/refresh` call.
- Browser refresh tokens are stored in the `insighta_refresh` HTTP-only cookie.
- CLI credentials are stored at `~/.insighta/credentials.json`.
- Logout revokes the active refresh token and clears browser credentials.

## Role Enforcement Logic

Users receive a role during GitHub login:

- GitHub logins listed in `INSIGHTA_ADMIN_GITHUB_LOGINS` become `admin`.
- Everyone else becomes `analyst`.

Profile list, search, export, and `/me` require authentication and either `admin` or `analyst`. Admin-only endpoints can use the shared `IsAdmin` permission in `core/auth_utils.py`.

## API Endpoints

Versioned Stage 3 routes:

- `GET /api/v1/auth/github/start?client=web|cli`
- `GET /api/v1/auth/github/callback`
- `POST /api/v1/auth/refresh`
- `POST /api/v1/auth/logout`
- `GET /api/v1/me`
- `GET /api/v1/profiles`
- `GET /api/v1/profiles/search?q=young males from nigeria`
- `GET /api/v1/profiles/export`

Legacy routes remain available under `/api/...` with the old pagination shape.

### Updated v1 Pagination Shape

```json
{
  "status": "success",
  "data": [],
  "pagination": {
    "page": 1,
    "limit": 10,
    "total": 2026,
    "total_pages": 203,
    "has_next": true,
    "has_previous": false
  }
}
```

## CLI Usage

Install locally:

```bash
pip install -e .
```

Commands:

```bash
insighta login --api http://localhost:8000/api/v1
insighta me
insighta profiles --gender female --min_age 25 --sort_by age --order asc
insighta search "young males from nigeria"
insighta export --output profiles.csv
insighta logout
```

The CLI stores credentials at `~/.insighta/credentials.json` and refreshes access tokens when needed.

## Web Portal

Open:

```text
http://localhost:8000/portal/
```

The portal uses Django sessions, CSRF-protected POST forms, and an HTTP-only refresh cookie. Users can search profiles, view results, export CSV, and log out.

The standalone web portal in `backendstage3-webportal/` uses `http://localhost:8000/api/v1` on localhost and `https://hng14stage3-backend.vercel.app/api/v1` when hosted at `https://backendstage3-webportal.vercel.app/`.

## Natural Language Parsing Approach

The parser is rule-based and does not use an LLM. It recognizes:

- Gender: `male`, `males`, `female`, `females`
- Age groups: `child`, `teenager`, `adult`, `senior`
- Young users: maps `young` to ages 16 through 24
- Age comparisons: `above 30`, `over 30`, `below 20`, `under 20`
- Country phrases: `from nigeria`, `from angola`, `from kenya`, `from benin`

Filters are combined into Django `Q` objects. If a query has no interpretable rule, the API returns an error.

## Rate Limiting And Logging

`RateLimitMiddleware` limits API traffic to 120 requests per IP per minute. `RequestLoggingMiddleware` records method, path, status code, user, IP address, duration, and timestamp in `RequestLog`.

## Seeding

Place profile data at `data/seed_profiles.json`, then run:

```bash
python manage.py seed_profiles
```
