# OMNIMIND AI

Phase 1 of [the PRD](OMNIMIND_AI_PRD.md): a React/TypeScript/Tailwind frontend and a FastAPI modular monolith backed by PostgreSQL. Authentication, organization membership, workspace access, and developer checks are implemented. Ingestion, retrieval, chat, graphs, and agents remain later phases.

## Quick start (Docker Desktop, PowerShell)

Prerequisites: Docker Desktop with Compose. No host Python or Node is required for this path.

```powershell
cd 'D:\projects\OMNIMIND AI'
Copy-Item .env.example .env
notepad .env
```

Replace `POSTGRES_PASSWORD` and the matching password in `DATABASE_URL` with a local password. For the example connection strings use URL-safe letters/numbers; otherwise URL-encode the password in URLs. `.env` is ignored. The supplied example explicitly enables local HTTP; never use it as production configuration.

```powershell
docker compose up --build -d
docker compose exec api .venv/bin/python -m app.provision --organization 'Acme' --slug acme --email admin@example.com
```

Provisioning prompts for a password without echo and confirms it. Minimum length is 12 characters. Rerunning the command preserves an existing user's credentials and membership role. Open **http://localhost:5173**, sign in, create a workspace, and sign out. Use `localhost` consistently: origins and cookies intentionally distinguish it from `127.0.0.1`. The API runs on http://localhost:8000; development OpenAPI is at `/docs`. API startup applies Alembic migrations before accepting requests.

```powershell
docker compose logs api
docker compose down
```

The database volume survives `down`. There are no seeded passwords or public registration endpoints.

## Host development

Install Python 3.12, uv 0.9.10, Node 24, and Docker Desktop. From the repository root after creating `.env`:

```powershell
docker compose up -d db
cd apps/api
uv sync --frozen --python 3.12
uv run alembic upgrade head
uv run python -m app.provision --organization 'Acme' --slug acme --email admin@example.com
uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 8000 --no-access-log --no-proxy-headers
```

In another terminal:

```powershell
cd 'D:\projects\OMNIMIND AI\apps\web'
npm ci
npm run dev
```

Vite and nginx forward `/api` to the backend; browser credentials stay on the frontend origin. Stop the Compose `api`/`web` services before running host servers on their ports.

Backend modules: `app/auth`, `app/organizations`, `app/workspaces`, `app/db`, and `app/core`. SQLAlchemy sessions are request scoped. Alembic's initial migration is an explicit schema snapshot; production startup never calls `create_all`.

## Configuration

Settings read process environment first, then the root `.env` when commands run from `apps/api`. Compose injects that file into the API container.

- `ENVIRONMENT`: defaults to `production`; `development` or `test` must be explicit.
- `DATABASE_URL`: SQLAlchemy `postgresql+psycopg://...` connection string. Production rejects the built-in placeholder.
- `COOKIE_SECURE`: defaults to `true`. Production requires it and HTTPS allowed origins. Secure sessions use the `__Host-` cookie prefix, HTTP-only, SameSite Lax, path `/`, and no Domain.
- `ALLOWED_ORIGINS`: JSON array of exact browser origins; no wildcards. Every mutation, including login, requires a matching `Origin` header. Authenticated mutations also require `X-CSRF-Token`.
- `ALLOWED_HOSTS`: JSON array of backend hostnames.
- `SESSION_HOURS`: absolute expiry, default 8; no sliding renewal.
- `LOGIN_LIMIT` / `LOGIN_WINDOW_SECONDS`: default 10 attempts per 900 seconds, per socket peer and per normalized account. PostgreSQL atomic counters work across API processes. All attempts count, including successful logins; expired buckets are cleaned on login.

Production requires a TLS reverse proxy, private PostgreSQL networking, unique database credentials, backups, and an explicitly configured origin/host. Compose is a loopback-only local development stack. Proxy headers are disabled to prevent spoofing limiter identity. Behind a proxy the peer limit is shared by proxy clients; tune limits or implement trusted edge rate limiting before deployment. Do not enable arbitrary forwarded headers. Avoid logging cookies, credentials, SQL parameters, or request bodies; supplied server commands disable access logs and SQLAlchemy hides bound parameters. No session or CSRF token is stored in browser persistent storage.

## Authorization assumptions

The PRD leaves workspace creation and workspace roles unspecified. Phase 1 uses these conservative rules:

- Organization admins list/read every workspace in their own active organization, create workspaces, list/change organization membership, and grant/revoke workspace access.
- Knowledge managers create workspaces. Creation grants the creator access. They can list/read only workspaces explicitly shared with them.
- Employees can list/read only workspaces explicitly shared with them; cannot create or manage access.
- Platform operators can see their organization membership and health endpoints, but have no workspace access or tenant administration authority. Infrastructure monitoring is outside the tenant API.
- Workspace membership has one role, `member`. It grants read access; it cannot elevate the organization role. No other workspace role exists in Phase 1.
- No organization creation, user disabling, password reset, or user deletion API is exposed. A trusted local operator provisions users and organizations. Administrators add **existing** user UUIDs to their organization via API; provisioning prints those UUIDs. Existing roles are changed through the guarded API, not by rerunning provisioning.
- Removing an organization membership cascades its workspace grants. Every subsequent request rechecks active user, active organization, and current membership. Removing/demoting the last active admin returns 409; organization row locking serializes competing administrator changes.

Composite foreign keys prevent grants referencing another tenant's workspace or a user without membership in that same organization. Unique constraints reject duplicate memberships, duplicate grants, and duplicate workspace names within an organization. Passwords use Argon2id; opaque session tokens contain 384 random bits and only their SHA-256 hashes are stored. CSRF tokens are derived from the secret session token, with their hashes stored in the session row; `/auth/csrf` restores the same token across tabs without exposing the session cookie. Login rotates an existing session; logout revokes it server-side before clearing the cookie.

## API usage

All application paths start `/api/v1`. Login requires an allowed `Origin` and JSON email/password. Preserve the returned cookie and the `csrf_token`; send `X-CSRF-Token` on POST/PATCH/DELETE. Reloaded clients call `GET /auth/me` then `GET /auth/csrf`.

- `POST /auth/login`, `POST /auth/logout`, `GET /auth/me`, `GET /auth/csrf`.
- `GET /organizations`: memberships of the caller, without trusting a client-selected tenant.
- `GET/POST /organizations/{organization_id}/members`: list or add (`user_id`, `role`).
- `PATCH/DELETE /organizations/{organization_id}/members/{user_id}`: change role (`role`) or revoke.
- `GET /workspaces?organization_id={id}&limit=50&after={next_cursor}`: authorized page (`items`, `next_cursor`). Treat cursor values as opaque and preserve them unchanged; each cursor's workspace is reauthorized. Maximum page size 100.
- `POST /workspaces`: `organization_id`, `name` (1–120 trimmed characters).
- `GET /organizations/{organization_id}/workspaces/{workspace_id}`: authorized details.
- `GET/POST /organizations/{organization_id}/workspaces/{workspace_id}/members`: list grants or add (`user_id`).
- `DELETE /organizations/{organization_id}/workspaces/{workspace_id}/members/{user_id}`: revoke grant.

Errors contain `code`, `request_id`, and `retryable`. Expired/invalid sessions return 401, missing/inaccessible tenants and workspaces return 404, denied actions on accessible resources return 403, conflicts return 409, validation returns 422, and login throttling returns 429 with `Retry-After`. Error responses never echo submitted credentials. Health endpoints are public: `/health/live` checks process availability; `/health/ready` checks database connectivity and migration state.

## Checks

Backend tests **require a disposable PostgreSQL database** and truncate application tables. Never point `TEST_DATABASE_URL` at data you want to keep. Create a separate database in the local container:

```powershell
cd 'D:\projects\OMNIMIND AI'
docker compose exec db sh -c 'createdb -U "$POSTGRES_USER" omnimind_test'
cd apps/api
$env:TEST_DATABASE_URL = 'postgresql+psycopg://omnimind:YOUR_LOCAL_PASSWORD@localhost:5432/omnimind_test'
uv sync --frozen --python 3.12
uv run ruff check .
uv run ruff format --check .
uv run mypy app
uv run pytest -q
uv run alembic check
uv run pip-audit
```

`alembic check` uses `DATABASE_URL`, normally the local development database, which must already be migrated. To verify reversible migrations, use only a disposable database, set `DATABASE_URL` to it and `ENVIRONMENT=test`, then run `uv run alembic downgrade base` followed by `uv run alembic upgrade head` and `uv run alembic check`. Downgrading deletes Phase 1 tables.

```powershell
cd 'D:\projects\OMNIMIND AI\apps\web'
npm ci
npm run check
npm test
npm run build
npm audit --audit-level=high
```

CI runs PostgreSQL integration tests, migration drift and rollback checks, Ruff formatting/lint, strict mypy, TypeScript/ESLint, frontend interaction tests, production build, and dependency audits. Tests cover two organizations, all four PRD roles, direct-ID tampering, duplicate/invalid database relationships, access revocation, concurrent last-admin demotion, CSRF, expiry, and session replay after logout. Frontend tests cover login/create/logout, organization switching with a late tenant response, session expiry, and access denial.

## Implementation verification (15 November 2026)

- Passed: 15 backend tests against a real disposable PostgreSQL 17 instance, Ruff lint/format, and strict mypy on all application modules.
- Passed: Alembic fresh upgrade, schema drift check, downgrade to base, and reapply.
- Passed: TypeScript/ESLint, all 4 frontend interaction tests, and Vite production build.
- Passed: `pip-audit` and `npm audit --audit-level=high`, both reporting no known vulnerabilities. Frontend dependencies include a patched `minimatch` override because the TypeScript ESLint dependency range otherwise resolves a vulnerable release.
- Passed: live HTTP smoke through Vite's proxy with random temporary credentials, using provisioning, login, two organization roles, authorized creation/listing, denied employee creation, logout, and cookie replay rejection.
- Not run here: Docker image builds/Compose startup (Docker is unavailable), hosted CI, and visual browser QA (the browser access host is unavailable). The live host stack was verified instead. Starlette emits one non-failing deprecation warning for its current `httpx` test adapter.

Temporary tooling, test databases, and installed dependencies live in ignored directories; no environment credentials or generated session tokens are committed. The existing PRD is unchanged. Implementation references: [SQLAlchemy composite foreign keys](https://docs.sqlalchemy.org/en/20/core/constraints.html) and [FastAPI explicit CORS configuration](https://fastapi.tiangolo.com/tutorial/cors/).
