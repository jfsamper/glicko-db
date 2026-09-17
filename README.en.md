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
- [License and attribution](#license-and-attribution)

## Features

- Public rankings, player search, profiles, match history, rating charts, and category conversion
- Glicko-2 rating calculation with configurable rating and category parameters
- Public interface in Spanish, English, and Portuguese
- Player and match administration with pagination, filters, and consistent ordering
- Public SGF record library, with match linking and unlinking for tournament directors, operators, and administrators
- Import of Excel workbooks (XLSX), OpenGotha XML, and match CSV files
- Tournament creation and editing, OpenGotha import, pairings, result entry, standings, and export
- Member account registration and individual result submission for administrative approval
- Public reports by period (default: all time) with player filters, localized CSV/PDF export, rating changes, and opponent, country, and club performance
- Admin-published news with quick links to players, tournaments, matches, and SGF records
- Swiss, category Swiss, accelerated Swiss, and McMahon systems
- BYE and absence handling, backups, restore safeguards, and SQLite migrations
- Draft tournaments hidden from public listings, with an administrative option to show drafts
- Handicap games in stones (Go style), with automatic suggestion by category gap and OGS-style rating adjustment

## User help

The interface guide is the reference for daily tasks. It is also available from the `?` button in the top navigation or at `/help?lang=en`.

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

The default values are in `config.py`.

- `APP_SECRET_KEY`: Flask session-signing key. It must be set in production.
- `ADMIN_PASSWORD`: current admin access password. Replace it in production.
- `LOAD_SAMPLE_DATA=1`: imports `rank-final.xlsx` if present and replaces the current dataset; intended only for local development.
- `DB_PATH`: SQLite database path, defined in `config.py`.
- `AUDIT_RETENTION_DAYS`: number of days to keep audit events; default is `730`.
- `MAIL_SERVER`, `MAIL_PORT`, `MAIL_USERNAME`, `MAIL_PASSWORD`, `MAIL_USE_TLS`, and `MAIL_FROM`: SMTP settings for password recovery; `PASSWORD_RESET_TTL_SECONDS` controls link expiry and defaults to 3600 seconds.
- `RECAPTCHA_SITE_KEY` and `RECAPTCHA_SECRET_KEY`: Google reCAPTCHA v3 keys for the member registration form. The secret key is verified on the server and must never be exposed to the browser.
- `RECAPTCHA_MIN_SCORE`: minimum accepted v3 score for registration; default is `0.5`.
- `RECAPTCHA_EXPECTED_HOSTNAME`: optional hostname check in the verification response; leave it empty if the key serves multiple configured hostnames.

- The default timezone is UTC-5. Each account can choose an IANA timezone; matches on the same day are processed by round number and then insertion order.
- `/reports` uses inclusive `start_date` and `end_date` ranges in the fixed server timezone. The page and CSV/PDF exports reuse the same filters and totals.
- Accounts use the roles `administrator`, `tournament_director`, `operator`, and `member`. Members submit results only for their linked player; the other roles review the approval queue.
- `/admin/settings` controls login limits and recovery expiry. In production, use HTTPS, unique passwords, and keep secrets in environment variables.
- SGF permissions allow administrators, directors, and operators to link or unlink records; only administrators can delete files.

## Project roadmap

The detailed roadmap is in [FUTURE_FEATURES.md](FUTURE_FEATURES.md). Import reconciliation, typed OpenGotha payloads, per-account audit filters, player profile improvements, and explicit tournament deletion are implemented. Remaining product work includes improving the BesoGo dark theme.

## Development

Regenerate the HTML guides after changing their Markdown sources:

```powershell
python scripts/build_help_html.py
```

Run the regression suite from the project root:

```powershell
pytest -q
```

### Code organization

Administrative routes are split by domain across `routes/admin_tournaments.py`, `routes/admin_matches.py`, `routes/admin_players.py`, and `routes/admin_users.py`. Tournament logic is split by responsibility across `services/tournament_gotha.py`, `services/tournament_participants.py`, `services/tournament_pairing.py`, `services/tournament_matches.py`, and `services/tournament_standings.py`. Translations and language selection live in `services/i18n.py`, while pure rating-chart helpers live in `services/chart_service.py`; `services/common.py` retains compatibility exports for existing imports. `services/tournament_service.py` remains a compatibility facade. The current full regression suite passes 373 tests.

### Linux hosting installation

Use Python 3.10 or later and create a new virtual environment before installing:

```bash
python3.11 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install --only-binary=Pillow -r requirements.txt
```

If this command reports that no compatible Pillow wheel exists, the Python version,
architecture, or Linux distribution selected by the host is unsupported. Select Python
3.10+ x86_64 in the hosting panel; do not compile Pillow without the system development
libraries for Python, JPEG, zlib, and freetype.

The tests cover ratings and charts, player filters, language support, backups, tournament migrations, pairing, standings, OpenGotha compatibility, result moderation, and public tournament pages.

SGF coverage includes metadata synchronization, missing-link repair, role permissions, deletion behavior, and backup restoration.

The consistent ordering, filtering, and search behavior is already shipped and validated across player, match, and tournament pages.

## License and attribution

Glicko DB was originally developed for the Go community in Colombia by Juan Felipe Samper in 2026.

The Glicko-2 system was published by Mark Glickman in 2022 into the public domain. The Python implementation is © 2009 Ryan Kirkman, and BesoGo is © 2015-2018 Ye Wang. Both are distributed under the [MIT license](static/vendor/besogo/LICENSE).
