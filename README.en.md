# Glicko DB

Documentation: [Español](README.md) | English | [Português](README.pt.md)

Glicko DB is a Flask and SQLite application for managing a Go community's players, ratings, matches, and tournaments. It provides public rankings and player statistics alongside protected administrative screens for imports, rating configuration, backups, and tournament operations.

## Features

- Public rankings, player search, profiles, match history, rating charts, and category conversion
- Configurable Glicko-2 rating and category parameters
- Public interface in Spanish, English, and Portuguese
- Player and match administration with pagination, filters, and consistent ordering
- Excel workbook, OpenGotha XML, and legacy CSV match imports
- Tournament creation and editing, OpenGotha import, pairings, result entry, standings, and export
- Member account registration and individual result submission for administrative approval
- Public reports, defaulting to All time, with player filters, localized CSV/PDF export, rating movement, and opponent, country, and club performance
- Swiss, category Swiss, accelerated Swiss, and McMahon pairing systems
- BYE and absence handling, backups, restore safeguards, and SQLite schema migrations
- Draft tournaments hidden from public listings, with an administrative option to show drafts
- Handicap games in stones (Go style), with an automatic suggestion from the category gap and an OGS-style rating adjustment

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

Open `http://127.0.0.1:5000`. The SQLite database is created at `data/acg_ratings.db` on first start. Set `LOAD_SAMPLE_DATA=1` only for local sample data; it replaces the current dataset when `rank-final.xlsx` exists.

## Configuration

The default values are in `config.py`.

- `APP_SECRET_KEY`: Flask session-signing key; set it in production.
- `ADMIN_PASSWORD`: current administrator password; override the development default in production.
- `LOAD_SAMPLE_DATA=1`: local-development sample import.
- `DB_PATH`: SQLite database path in `config.py`.
- `AUDIT_RETENTION_DAYS`: number of days to retain audit events; defaults to `730`.
- `MAIL_SERVER`, `MAIL_PORT`, `MAIL_USERNAME`, `MAIL_PASSWORD`, `MAIL_USE_TLS`, and `MAIL_FROM`: SMTP settings for password recovery; `PASSWORD_RESET_TTL_SECONDS` controls link expiry and defaults to 3600 seconds.

Application-generated dates and times use UTC-5 by default. Each account can choose an IANA time zone in user management; accounts without a preference keep UTC-5. The timezone selectors show one representative for each commonly used current UTC offset, sorted from lowest to highest. Python uses the system IANA database on Linux, while `tzdata` provides the portable fallback on Windows or minimal Linux images. If an account's stored zone cannot be loaded, display falls back to UTC-05:00. When ratings are calculated, matches on the same day are processed by round number and then insertion order; unknown rounds are treated as round 1.

Reports at `/reports` use inclusive `start_date` and `end_date` boundaries, and period membership uses the fixed server timezone (UTC-5 by default), not the account timezone. General and player-filtered reports default to All time. Win percentage is wins divided by games. Each row includes absolute rating points, percentage rating change, and full-integer category change. The player selector is ordered by total games. Totals are calculated once on the server and reused by the page and CSV/PDF exports; PDF labels and text use the current language, while records with invalid dates or results are excluded and counted. Matches materialized from tournaments retain one unique identity per pairing so they cannot be imported or counted twice.

Administration uses named accounts with four roles: `administrator`, `tournament_director`, `operator`, and `member`. New self-registered accounts receive the `member` role; an administrator can manually link them to a player at `/admin/users`. Members can submit only games involving their linked player. Directors, operators, and administrators review the queue at `/admin/result-submissions`; only approved results become public matches. When no account exists, the app bootstraps one initial administrator from `ADMIN_PASSWORD` on first start; manage additional accounts and their time zones at `/admin/users`. Users can open `/admin/profile` to save their language, theme, time zone, email, and password. The recovery link on `/admin/login` uses single-use tokens and non-enumerating responses; configure SMTP in production. Failed attempts are rate limited; production should use HTTPS and strong, unique passwords. Authorization is based on the user session and role permissions. Only `administrator` and `operator` roles can modify players, ratings, and categories; `tournament_director` retains tournament operations.
Administrators can adjust the maximum login attempts, rate-limit window, and recovery-link lifetime at `/admin/settings`. These values are stored in SQLite, and the reset button restores the initial values from `config.py`. `ADMIN_PASSWORD`, paths, and SMTP credentials remain environment configuration.

## Project roadmap

The detailed and prioritized roadmap is in [FUTURE_FEATURES.md](FUTURE_FEATURES.md). Explicit import reconciliation, the typed OpenGotha payloads, free-text and date-filtered per-account audit review, the player profile enhancement, and the explicit tournament-delete modal are implemented and verified. Profiles include recent activity, streaks, tournament history, and a season filter.

## Operations

### Import ratings and matches

1. Sign in at `/admin/login`.
2. Open the import screen.
3. Upload one of these supported formats:

  - `.xlsx` or `.xls`: imports the data and replaces the current dataset.
  - OpenGotha `.xml`: imports matches and tournament metadata. Each game's `handicap` attribute (stones given to Black) is kept if present.
  - `.csv`: requires the `date`, `white`, `black`, and `result` columns. An optional `handicap` column (stones, 0-9) is kept; missing or invalid values default to 0.
4. Confirm the resulting rankings and player profiles.

Keep a backup before importing a workbook that replaces the data.

### Run a tournament

1. In administration, create a tournament or import an OpenGotha XML file.
2. Add participants and choose Swiss, category Swiss, accelerated Swiss, or McMahon.
3. Generate or manually manage pairings for each round.
4. From the tournament screen, edit the name, location, round count, BYE points, and absence points.
5. Enter results by clicking the winning player name or the result text. The text cycles through `-`, `1-0`, `1/2-1/2`, and `0-1`; clicking the selected winner again clears it. The winner is highlighted in bold green.
6. Record BYEs and absences, generate the next round, review standings, and export results with the administration buttons.

Standings positions are always unique and sequential; ties resolve through SOS, SOSOS, SODOS, rating, and name. The pairing algorithm avoids repeating a BYE for a player while another participant has not received one, and imported OpenGotha BYEs are recorded so future rounds respect that history.

When an OpenGotha import finds a similar name, it shows a database player suggestion. Click the suggested name to link it immediately to the existing player, or use the selector to create a new player or choose another player.

Each pairing gets an automatic handicap suggestion in stones (one stone per category of rating gap between the players), which the tournament director can edit before entering the result. When the round is processed, the handicap carries over to the match and adjusts ratings OGS-style: the opponent's rating is shifted only for that match's calculation, never touching their base rating.

### View reports

Open `/reports` to choose a year, quarter, month, All time, or custom range. The table shows players with valid games in the period and links to performance against each opponent. It also shows aggregates by opponent country and club. The CSV and PDF links preserve the selected filters and use the same totals shown on screen; PDF filenames include the player and period.

### Report results

Use `Create account` on the login page, then ask an administrator to link the account to a player at `/admin/users`. The report form accepts only games involving that linked player. Submissions remain pending and do not affect rankings, ratings, or reports until approved by a director, operator, or administrator at `/admin/result-submissions`. The schema and service helpers already provide hashed, expiring, single-use codes for a future email approval flow; automatic publication by code remains disabled until verification policy is defined.

When round results are materialized in the main matches table, the `event` column preserves the tournament or event name. The `notes` column, shown as `Round` in the interface, stores the round number in canonical form as a bare integer, such as `5` (not `Round 5`). If the entry is in a legacy format, such as `15:00:00`, it is preserved and converted to a numeric round. If no numeric value is found, the text is kept and treated as `0`.

Tournament tables are migrated automatically at startup to preserve compatibility with existing databases.

When ratings are recalculated, round order is respected within each day in both full recalculation and incremental update. If the round cannot be determined, round 1 is used.

### Review the administrative audit log

1. Sign in at `/admin/login`.
2. Open the admin dashboard and use the audit option.
3. Filter by user or action to review changes in players, matches, ratings, imports, users, and configuration.

The audit view keeps the activity history for each account and helps determine who made each change before recovery or support actions.

The audit log records successful state-changing administrator actions, including imports, tournament lifecycle and result operations, player and match changes, rating and category changes, user management, and backup operations. It keeps a compact JSON summary, limits details to 2 KiB per event, and removes entries older than 730 days by default. Set `AUDIT_RETENTION_DAYS` before startup to choose a different positive retention period.

### Back up and restore

Use the admin backup screen before bulk imports, restores, or upgrades. The server generates and validates backup filenames, and restored databases pass through the application's migration path. Restore also rebuilds the player search index and only considers application-managed backups or the designated `.bak` fallback; temporary files in `data/` are never used as restore sources.

## Development

Run the regression suite:

```powershell
pytest -q
```

### Installation on Linux hosting

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

Tests cover ratings and charts, player filters, language support, backups, tournament migrations, pairing, standings, OpenGotha compatibility, result moderation, and public tournament pages.

The consistent ordering, filtering, and search behavior is already shipped and validated across player, match, and tournament pages.

## Recommended next features

1. Pagination improvements. Show total pages, current-page context, and a simple results-per-page selection.
2. Player profile with tournament results and statistics. Add an overview card for each player showing tournaments played, win/loss record, event results, recent tournament table, streaks, and performance percentages, with filters by category and season.
3. Scheduled backups with retention and restore verification. Keep them disabled by default and enable them only when a clear retention policy exists.

## License and Attribution

Review the source files and dependencies for licensing details. The Glicko-2 implementation was originally developed by Ryan Kirkman and released under the MIT license.