# Marks Checker 3

Marks Checker 3 is a Voila.ca bulk article checker that combines the resiliency of the original Marks Checker with the scraping fallbacks from `voilascrape`. Paste a `global_sid` cookie, drop in article numbers, and the app resolves the correct region, fetches product details (with HTML fallbacks when the API refuses to help), and keeps results in SQLite for later download.

## Features

- Region auto-detection using the Voila cart API with regex fallback.
- Multi-threaded chunk processing with per-chunk progress.
- HTML fallback scraper for stubborn articles (parses `window.__INITIAL_STATE__` when JSON fails).
- Persists sessions/results in SQLite (`instance/markschecker.db` by default).
- One-command developer setup (`./scripts/dev.sh`).
- Production helper for Ubuntu/Debian with Gunicorn+Nginx+Certbot (`scripts/deploy_ssl.sh`).

## Quick Start

```bash
git clone https://github.com/Nacnud88/markschecker3.git
cd markschecker3
./scripts/dev.sh
```

The script will create a virtualenv, install dependencies, and run Flask in debug mode. Visit `http://127.0.0.1:5000`.

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `MARKSCHECKER_BASE_DIR` | `./instance` (dev) or `/opt/markschecker3/instance` (prod) | Where the SQLite DB and other writable files live. |
| `MARKSCHECKER_DB_PATH` | `<BASE_DIR>/markschecker.db` | Override the database location. |
| `MARKSCHECKER_MAX_WORKERS` | `4` | Maximum concurrent Voila requests per chunk. |
| `MARKSCHECKER_REQUEST_TIMEOUT` | `15` | Seconds before HTTP requests time out. |
| `MARKSCHECKER_CHUNK_SIZE` | `400` | Terms per chunk. |
| `MARKSCHECKER_SECRET` | `change-me` | Flask secret key. Set this in production. |

Create an `.env` or export variables before running if you need different values.

## Production Deployment (Ubuntu/Debian)

For a push-button install on a fresh droplet:

```bash
sudo ./scripts/deploy_ssl.sh
```

The script will:

1. Install Python, Git, Gunicorn, Nginx, and Certbot.
2. Clone the repository into `/opt/markschecker3/current`.
3. Create a virtualenv and install dependencies.
4. Configure a systemd service (`markschecker3.service`) bound to `127.0.0.1:5100`.
5. Set up an Nginx reverse proxy and optionally request a Let’s Encrypt certificate.

Re-run the script to update (it does a `git pull` + reload). For zero-downtime deploys, swap the `current` symlink after verifying a new release.

## API Endpoints

- `POST /api/sessions` – start a session (`globalSid`, `searchTerm`, `searchType`, `limit`).
- `POST /api/sessions/<id>/chunks` – process one chunk (`terms`, `globalSid`, `limit`).
- `GET /api/sessions/<id>/results` – fetch stored products plus session summary.
- `GET /api/sessions/<id>` – check session progress.

All payloads/response structures mirror the UI usage.

## Development Notes

- The UI lives in `app/templates/index.html`. Static assets are under `app/static`.
- Database helpers are in `app/db.py`; search logic (`SearchService`) lives in `app/search_service.py`.
- Gunicorn config defaults to `workers = max(2, cpu_count())`, `bind = 127.0.0.1:5100`.
- Run the diagnostics helper (`/root/diagnose_system.sh`) on a server to inspect running services, venvs, ports, timers, etc.

## License

MIT © 2025 Nacnud88
