# Glicko DB

Documentation: [Español](README.md) | English | [Português](README.pt.md)

Glicko DB is a Flask and SQLite application for managing a Go community's players, ratings, matches, and tournaments. It provides public rankings and statistics, along with protected administrative screens for imports, rating configuration, backups, and tournament operations.

## Table of contents

- [Features](#features)
- [User help](#user-help)
- [Requirements](#requirements)
- [Local run](#local-run)
- [Configuration](#configuration)
- [Project roadmap](#project-roadmap)
- [Development](#development)
  - [Code organization](#code-organization)
  - [Linux hosting installation](#linux-hosting-installation)
  - [Scheduled backups and restore drills](#scheduled-backups-and-restore-drills)
- [License and attribution](#license-and-attribution)

## Features

- Public rankings, player search, profiles, match history, and rating charts
- Configurable Glicko-2 ratings and an interface in Spanish, English, and Portuguese
- Excel, CSV, and OpenGotha imports; player, match, and tournament administration
- Swiss, category Swiss, accelerated Swiss, and McMahon tournament systems
- Member registration and result submissions for administrative approval
- Filterable reports with CSV/PDF export, news, and a public SGF library
- Backups, protected restores, and SQLite migrations
- Country, regional, age, and female-player rankings
- Handicap games, BYEs, and absences
- Draft and canceled tournaments stay hidden; completed unrated tournaments show their games publicly without affecting ratings

## User help

The interface guide is the reference for daily tasks. It is also available from the `?` button in the top navigation or at `/help?lang=en`.
On supported mobile browsers, you can add Glicko DB to your home screen as an app.

- [User interface help](docs/user_interface.en.md)
- [Routes and integration notes](docs/api_endpoints.en.md)

## Requirements

- Python 3.10 or later
- `pip`
- Python packages:
  - `Flask>=3.0`
  - `Flask-WTF>=1.2`
  - `Werkzeug>=3.0`
  - `openpyxl>=3.1`
  - `reportlab>=4.0`
  - `Pillow==11.3.0` (required by ReportLab for PDF generation)
  - `tzdata>=2024.1` (Windows time zone data)

## Local run

Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
$env:APP_SECRET_KEY = "replace-with-a-long-random-value"
$env:ADMIN_PASSWORD = "choose-a-strong-password"
python app.py
```

macOS or Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
export APP_SECRET_KEY="replace-with-a-long-random-value"
export ADMIN_PASSWORD="choose-a-strong-password"
python app.py
```

Open `http://127.0.0.1:5000` in the browser. The application creates the SQLite database at `data/acg_ratings.db` on first start.

Only for local sample data, define `LOAD_SAMPLE_DATA=1` before startup. Do not use sample data in a production database.

## Configuration

Set required environment variables as needed; defaults are in `config.py`.

- `APP_SECRET_KEY` and `ADMIN_PASSWORD`: session key and one-time password for creating the initial administrator account. Use secure values in production.
- `APP_PUBLIC_ORIGIN`: public HTTPS origin for password-recovery links.
- `DB_PATH`: optional SQLite database path.
- `MAIL_SERVER`, `MAIL_PORT`, `MAIL_USERNAME`, `MAIL_PASSWORD`, `MAIL_USE_TLS`, and `MAIL_FROM`: SMTP settings for recovery and alerts.
- `RECAPTCHA_SITE_KEY` and `RECAPTCHA_SECRET_KEY`: reCAPTCHA v3 keys for member registration.
- `LOAD_SAMPLE_DATA=1`: loads local sample data; do not use in production.
- `AUDIT_RETENTION_DAYS` and `SCHEDULED_BACKUP_KEEP`: audit and scheduled-backup retention.

The default timezone is UTC-5. Use HTTPS in production and keep secrets out of source control.

## Project roadmap

See the [project roadmap](FUTURE_FEATURES.md), which consolidates product and operational proposals and the [detailed authenticated API migration plan](FUTURE_FEATURES.md#authenticated-api-migration). The API is planned, not implemented; existing public web pages and exports remain the available interfaces.

## Development

Help pages are rendered from Markdown. To export static HTML copies:

```powershell
python scripts/build_help_html.py
```

Run tests from the project root:

```powershell
pytest -q
```

Optional Chromium navigation tests require Playwright:

```powershell
python -m pip install playwright
python -m playwright install chromium
python -m pytest tests\test_navigation_browser.py -q
```

### Code organization

Application routes are in `routes/`, shared logic in `services/`, and tests in `tests/`. Test fixtures are versioned with the project.

### Linux hosting installation

Use Python 3.10 or later, create a virtual environment, and install dependencies:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install --only-binary=Pillow -r requirements.txt
```

If Pillow has no compatible wheel, check that the host's Python version and platform are supported. For common deployment issues, check server logs, the Python environment, dependencies, WSGI entry point, permissions, and routing. Optional diagnostic tools are in `scripts/dev_only/`; do not include them in normal deployments.

### Scheduled backups and restore drills

Schedule `scripts/scheduled_backup.py` with the same Python runtime, working directory, and environment (`DB_PATH` and `SCHEDULED_BACKUP_KEEP`) used by the application. The job verifies the backup and a restore into a temporary database before applying retention; it exits with status 1 on failure. In Windows Task Scheduler, configure:

- **Program**: `C:\path\to\glicko-db\.venv\Scripts\python.exe`
- **Arguments**: `scripts\scheduled_backup.py`
- **Start in**: `C:\path\to\glicko-db`

On Linux, a daily cron entry can be:

```cron
0 2 * * * cd /srv/glicko-db && .venv/bin/python scripts/scheduled_backup.py >> /var/log/glicko-db-backup.log 2>&1
```

Set the same production environment variables in the scheduler; cron and Task Scheduler may not inherit the web server's environment. As an administrator, review `/admin/backups` for rating freshness, the latest backup and restore check, and operational failures. Practice restoration on a disposable instance. Before replacing the active database, schedule a maintenance window, block new writes, and let in-flight requests finish during the final swap. SQLite backups do not include `uploads/sgf`; protect and restore that directory separately.

## License and attribution

Glicko DB was originally developed for the Go community in Colombia by Juan Felipe Samper in 2026.

The Glicko-2 system was published by Mark Glickman in 2022 into the public domain. The Python implementation is © 2009 Ryan Kirkman, and BesoGo is © 2015-2018 Ye Wang. Both are distributed under the [MIT license](./static/vendor/besogo/LICENSE).
