# EventHub Pro — Backend

Django 5.2 REST API for EventHub Pro (JWT auth, events, GoHighLevel integration).

## Quick start

```bash
python -m venv venv
venv\Scripts\activate          # Windows
# source venv/bin/activate     # Mac/Linux

pip install -r requirements.txt
cp .env.example .env           # then edit values
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver     # http://localhost:8000
```

API base URL: `http://localhost:8000/api`

## Environment

Copy `.env.example` to `.env`. Required for full functionality:

| Variable | Purpose |
|---|---|
| `SECRET_KEY` | Django secret key |
| `CORS_ALLOWED_ORIGINS` | Frontend URL(s), comma-separated |
| `GHL_PRIVATE_TOKEN` | GoHighLevel API token |
| `GHL_LOCATION_ID` | GoHighLevel location ID |
| `AUTO_LOGIN_SECRET` | Magic-link login secret (match frontend) |

By default the app uses **SQLite** (`db.sqlite3`). To use PostgreSQL, uncomment the PostgreSQL `DATABASES` block in `config/settings.py`.

## Apps

- `accounts` — custom User model, JWT auth, profiles, roles
- `events` — events, occurrences, attendance
- `ghl` — GoHighLevel sync (server-side only)
