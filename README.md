# Microservices Web App

A FastAPI microservices application that registers and authenticates users,
and scrapes Moldovan company data by IDNO from `openmoney.md` and
`depozitar.statistica.md` through an ELT pipeline.

## Services

| Service | Port | Responsibility | Owns data |
| --- | --- | --- | --- |
| `gateway` | 8000 | Single entry point for clients. Validates the bearer token against `user_service` and forwards every request to `app_service`. | no |
| `app_service` | 8001 | Orchestrator. Routes forwarded requests to `user_service` or `scraper_service` and aggregates their responses. | no |
| `user_service` | 8002 | Registration, OAuth2 authentication and token generation, session handling. | `user_service` schema |
| `scraper_service` | 8003 | Scrapes company data by IDNO and runs the ELT pipeline. | `scraper_service` schema |

Shared infrastructure: PostgreSQL for persistence, Redis for caching.

## Repository layout

```
backend/
  gateway/            entry point and auth enforcement
  app_service/        orchestration and data processing
  user_service/       users, OAuth2, sessions
  scraper_service/    extract, load, transform
frontend/             React client
db/init/              schema and role bootstrap
docs/
  architecture/       architecture diagram
  screenshots/        Swagger UI captures
```

## Database layout

A single PostgreSQL instance holds one schema per data-owning service, each
owned by its own login role:

| Schema | Role | Tables |
| --- | --- | --- |
| `user_service` | `user_service_app` | `users`, `refresh_tokens` |
| `scraper_service` | `scraper_service_app` | `source_data`, `transformed_data`, `scrape_jobs` |

A role has no privileges on the other role's schema, so a service cannot read
another service's tables even by accident. `gateway` and `app_service` are
stateless and hold no database credentials at all.

## Setup

Requires Docker and Docker Compose.

```bash
cp .env.example .env     # then edit the placeholder passwords
docker compose up -d
```

Check that the infrastructure is healthy:

```bash
docker compose ps
```

Roles and schemas are created on the first start of the PostgreSQL container
by `db/init/01_init_schemas.sh`. To recreate them from scratch,
drop the volume:

```bash
docker compose down -v && docker compose up -d
```

## Configuration

Every service ships an `.env.example` next to its `Dockerfile`, and the root
`.env.example` holds the values `docker-compose` needs. No `.env` file is
committed.

## Auth flow

Documented once `user_service` and `gateway` are in place.

## Scraping

Documented once `scraper_service` is in place.

## ELT process

Documented once `scraper_service` is in place.

## Architecture diagram

See `docs/architecture/`.

## API documentation

Each service exposes auto-generated Swagger UI at `/docs`. Captures are in
`docs/screenshots/`.

## Deployment

Documented alongside `docker-compose.prod.yml`.

## Code style

Python code follows PEP 8 and `snake_case`, with type hints throughout. The
React frontend follows the JavaScript and TypeScript conventions of its own
ecosystem, so component and variable names there are `PascalCase` and
`camelCase` by design.

## TODO and ideas

Collected as the implementation progresses.
