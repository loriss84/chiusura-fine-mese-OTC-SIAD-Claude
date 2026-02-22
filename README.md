# Month-End Close Tracker (OTC/SIAD)

A multi-country, multi-tenant Django web application for managing month-end close processes.

## Technology Stack

- **Python 3.12**
- **Django 5.0** + **Django REST Framework**
- **PostgreSQL** (no Docker)
- **Frontend**: Django Templates + **HTMX** (no React)
- **Auth**: Django built-in auth (OIDC/Azure Entra ID-ready design)

## Countries Supported

Austria (AT), Germany (DE), Czech Republic (CZ), Slovakia (SK), Romania (RO),
Bulgaria (BG), Poland (PL), Hungary (HU), Italy (IT)

## Business Context

Each country has **Macro Areas** (AR, AP, GL, …), each containing **Processes**
with atomic **Tasks**. Monthly **Close Cycles** are created from a published
**Template Version** and generate **ProcessRun / TaskRun** records per country.

---

## Local Development Setup

Two paths depending on your OS and setup:

---

### ⚡ Quick Start – Windows / SQLite (no database install needed)

> Works with **any Python version** (3.11, 3.12, 3.13, 3.14+). No PostgreSQL required.

```powershell
git clone <repo-url>
cd chiusura-fine-mese-OTC-SIAD-Claude

python -m venv .venv
.venv\Scripts\activate

# Install lightweight deps (no psycopg2):
pip install -r requirements-dev-windows.txt

# Configure – SQLite is the default, nothing to change:
copy .env.example .env
```

The default `.env` already uses `DATABASE_URL=sqlite:///db.sqlite3`.
Skip straight to [step 4 – migrations](#4-run-migrations).

---

### Full Setup – Linux / macOS / Windows with PostgreSQL

#### Prerequisites

- Python 3.12 (recommended; psycopg2-binary has no wheel for Python 3.14 yet)
- PostgreSQL (any recent version, installed natively)

#### 1. Clone & virtual environment

```bash
git clone <repo-url>
cd chiusura-fine-mese-OTC-SIAD-Claude

python3.12 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt  # includes psycopg2-binary
```

#### 2. Create PostgreSQL database

```bash
# As the postgres superuser:
psql -U postgres -c "CREATE USER closetracker WITH PASSWORD 'closetracker';"
psql -U postgres -c "CREATE DATABASE closetracker_db OWNER closetracker;"
psql -U postgres -c "GRANT ALL PRIVILEGES ON DATABASE closetracker_db TO closetracker;"
# For running tests (allows creating test_closetracker_db):
psql -U postgres -c "ALTER USER closetracker CREATEDB;"
```

#### 3. Configure environment

```bash
cp .env.example .env
# Edit .env – uncomment the PostgreSQL line and comment out the SQLite one:
#   DATABASE_URL=postgres://closetracker:closetracker@localhost:5432/closetracker_db
```

### 4. Run migrations

```bash
python manage.py migrate
```

### 5. Create superuser (optional – seed creates one)

```bash
python manage.py createsuperuser
```

### 6. Seed demo data

```bash
python manage.py seed_demo
```

This creates:
- 9 countries
- Demo users: `admin`, `hq_owner`, `cfo_it`, `process_owner_it` (password: `demo1234`)
- Template Family "Standard OTC/SIAD Close Template" v1 (PUBLISHED)
  - Macro AR: Vendite Bulk (gating), Vendite Package
  - Macro AP: Ricevimenti, Chiusura ordini di acquisto
  - Dependencies: AR/Vendite Bulk → AP/Ricevimenti, AR/Vendite Package → AP/Chiusura ordini
- CloseCycle 2026-03 with final deadline 2026-03-10

### 7. Run development server

```bash
python manage.py runserver
```

Open: http://127.0.0.1:8000/

---

## Demo Accounts

| Username | Password | Role | Access |
|----------|----------|------|--------|
| `admin` | `demo1234` | Admin | Full access, Django admin |
| `hq_owner` | `demo1234` | HQ Project Owner | Global dashboard, all countries |
| `cfo_it` | `demo1234` | Country CFO | Italy only, can override due dates |
| `process_owner_it` | `demo1234` | Process Owner | Assigned tasks in Italy only |

---

## Application URLs

| URL | Description |
|-----|-------------|
| `/login/` | Login page |
| `/dashboard/` | Redirects to HQ or Country dashboard |
| `/dashboard/hq/` | HQ heatmap + overdue/blocked list |
| `/dashboard/country/` | Country overview (auto-selects first accessible) |
| `/dashboard/country/<code>/` | Specific country dashboard |
| `/my-tasks/` | My open tasks + quick HTMX status update |
| `/cycles/` | Cycle list |
| `/cycles/create/` | Create new cycle (HQ/Admin only) |
| `/cycles/<id>/country/<code>/` | Cycle × Country detail |
| `/cycles/<id>/export/` | CSV export (HQ/Admin only) |
| `/task/<id>/` | Task detail: update status, comment, evidence, override |
| `/admin/` | Django admin for template management |

---

## REST API Endpoints

All endpoints require session authentication.

| Method | URL | Description |
|--------|-----|-------------|
| GET | `/api/countries/` | List accessible countries |
| GET | `/api/cycles/` | List cycles |
| GET | `/api/cycles/<id>/` | Cycle detail |
| GET | `/api/cycles/<id>/process-runs/` | Process runs for cycle |
| GET | `/api/task-runs/` | Task runs (filterable: `?mine=1`, `?overdue=1`, `?cycle_id=X`) |
| GET | `/api/task-runs/<id>/` | Task run detail |
| PATCH | `/api/task-runs/<id>/update/` | Update status/comment/override |
| POST | `/api/task-runs/<id>/evidences/` | Add evidence link |
| GET | `/api/dashboard/hq/` | HQ summary stats |

---

## Architecture

### Data Model Overview

```
TemplateFamily
  └── TemplateVersion (DRAFT / PUBLISHED)
        ├── MacroTemplate (AR, AP, GL, …)
        │     └── ProcessTemplate (is_gating, order)
        │           └── TaskTemplate (due_offset_days, requires_evidence)
        ├── Dependency (DAG: ProcessTemplate → ProcessTemplate)
        ├── ProcessAssignment (country × process → owner)
        └── TaskAssignment (country × task → owner, optional)

CloseCycle (period, final_deadline, template_version)
  └── ProcessRun (cycle × country × process_template, status, completion%)
        └── TaskRun (due_date_calculated, override, status, evidence)
              ├── Evidence (link_url)
              └── TaskRunLog (append-only audit)

Country / Membership (user × country × role) / UserGlobalRole
NotificationOutbox (MVP: stored, not sent)
```

### Roles

| Role | Scope | Permissions |
|------|-------|-------------|
| `ADMIN` | Global | Full access + template management + publish |
| `HQ_PROJECT_OWNER` | Global | Read all, override due dates anywhere |
| `COUNTRY_CFO` | Per country | Read/write own country, override due dates |
| `PROCESS_OWNER` | Per country | Only assigned tasks |

### Country Isolation

Every run model (`ProcessRun`, `TaskRun`) is filtered by `country_id`.
The `assert_country_access(user, country)` function enforces access at
every view entry point. The `CountryScopedQuerySet` mixin can be used
to apply user-scoped filters at the ORM level.

### Template Versioning

- Templates are immutable once **PUBLISHED**
- Each modification requires creating a new `TemplateVersion` (DRAFT)
- `validate_no_dependency_cycle()` runs a DFS before publish
- Each `CloseCycle` is permanently linked to one published `TemplateVersion`

---

## Running Tests

```bash
python manage.py test tracker --verbosity=2
```

29 tests covering:
- Country isolation (multi-tenant security)
- Due date override permissions per role
- Effective due date calculation (offset + override)
- DAG cycle detection (prevents circular dependencies)
- ProcessRun status aggregation logic

---

## Production Deployment (Gunicorn + Nginx + systemd)

### 1. Environment

```bash
# Production .env
SECRET_KEY=<long-random-string>
DEBUG=False
ALLOWED_HOSTS=yourdomain.com,www.yourdomain.com
DATABASE_URL=postgres://closetracker:<password>@localhost:5432/closetracker_db
```

### 2. Collect static files

```bash
python manage.py collectstatic --noinput
```

### 3. Gunicorn systemd service

Create `/etc/systemd/system/closetracker.service`:

```ini
[Unit]
Description=Month-End Close Tracker (Gunicorn)
After=network.target

[Service]
User=www-data
Group=www-data
WorkingDirectory=/var/www/closetracker
EnvironmentFile=/var/www/closetracker/.env
ExecStart=/var/www/closetracker/.venv/bin/gunicorn \
    --workers 3 \
    --bind unix:/run/closetracker.sock \
    config.wsgi:application
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable closetracker
sudo systemctl start closetracker
```

### 4. Nginx configuration

Create `/etc/nginx/sites-available/closetracker`:

```nginx
server {
    listen 80;
    server_name yourdomain.com www.yourdomain.com;

    # Redirect to HTTPS
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl http2;
    server_name yourdomain.com www.yourdomain.com;

    ssl_certificate     /etc/letsencrypt/live/yourdomain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/yourdomain.com/privkey.pem;

    location /static/ {
        alias /var/www/closetracker/staticfiles/;
        expires 30d;
    }

    location / {
        proxy_pass http://unix:/run/closetracker.sock;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

```bash
sudo ln -s /etc/nginx/sites-available/closetracker /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

### 5. Future: OIDC / Azure Entra ID

The authentication layer is designed for extensibility. To add Azure Entra ID:

1. Install `mozilla-django-oidc` or `social-auth-app-django`
2. Add `AUTHENTICATION_BACKENDS` pointing to the OIDC backend
3. Map Azure group claims to `Membership` / `UserGlobalRole` records
4. The existing permission system (`permissions.py`) requires no changes

---

## Project Structure

```
.
├── config/
│   ├── settings.py
│   ├── urls.py
│   └── wsgi.py
├── tracker/
│   ├── models.py            # All data models
│   ├── permissions.py       # Role checks + CountryScopedQuerySet
│   ├── services.py          # Business logic (publish, create cycle, update task)
│   ├── views.py             # Django views (HTML + HTMX)
│   ├── api_views.py         # DRF API views
│   ├── serializers.py       # DRF serializers
│   ├── urls.py              # URL patterns
│   ├── admin.py             # Django admin
│   ├── context_processors.py
│   ├── tests.py             # 29 automated tests
│   └── management/
│       └── commands/
│           └── seed_demo.py
├── templates/
│   ├── base.html
│   └── tracker/
│       ├── login.html
│       ├── hq_dashboard.html
│       ├── country_dashboard.html
│       ├── my_tasks.html
│       ├── cycle_list.html
│       ├── cycle_create.html
│       ├── cycle_country_detail.html
│       ├── task_detail.html
│       ├── no_country.html
│       └── partials/
│           ├── task_row.html      (HTMX partial)
│           └── task_status_row.html
├── static/
│   └── css/app.css
├── requirements.txt
├── .env.example
└── manage.py
```
