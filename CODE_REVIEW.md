# Code Review – glicko-db

This review is intentionally short and action-oriented. The project is currently green: the full suite passes with 337 tests, and the remaining work is mainly follow-up cleanup rather than new production risk.

## 1. Critical bugs

- OpenGotha import robustness: missing tournament metadata and missing player-name attributes are now guarded; malformed XML is rejected without crashing the import path.
- McMahon import correctness: `mm_bar`, `mm_floor`, and `mm_zero` remain in sync with the imported tournament values; wrong baselines are no longer applied during conversion.
- Date validation: malformed match dates are rejected instead of being silently stored; this prevents follow-on DB errors and 500s during filtering or ranking updates.
- Admin traceability gate: login/logout and sensitive config/user changes are now recorded in the SQLite `audit_log` table; named-user auth remains enforced.

## 2. High-priority issues

- Backup + repair hardening: stale legacy SQLite schemas are repaired automatically, FTS5 search tables are rebuilt safely, and restore candidates are limited to managed backups.
- Tournament integrity: BYE handling, standings rank uniqueness, and round-pairing logic were corrected and covered by regression tests.
- Rating correctness: recomputation and dirty-state replay are transaction-safe, use UTC-5 application dates, and process same-day matches by round; unknown rounds are treated as round 1.
- Security baseline: CSRF, secure session cookies, and user-role-based admin access are enforced; only administrators and operators can modify player, rating, and category data; the legacy shared-password bridge has been removed.
- Account recovery: authenticated users can manage email, language, theme, timezone, and password from their profile; forgotten-password requests use hashed, expiring, single-use tokens and generic responses to avoid email enumeration.

## 3. Medium-priority issues

- Config validation: category and rating settings now reject invalid or non-positive values instead of persisting broken data.
- Login throttling configuration: attempt and time-window thresholds now come from `config.py` with environment-backed defaults.
- Tournament UX: async redirect flow, settings editing, result entry behavior, and pending-player resolution were improved without breaking the normal admin workflow.
- Search consistency: player, match, and tournament list pages now share a consistent ordering and filtering model, with query state preserved across pagination, and this is covered by regression tests.
- Performance cleanup: lookup caching, SQL `LIMIT/OFFSET` use, and migration guards were tightened to reduce unnecessary scanning and repeated work.
- Import reliability: fuzzy player matching, round-note normalization, and metadata handling were hardened across workbook and XML imports. OpenGotha tournament metadata and match records now use typed `GothaTournamentPayload`, `GothaPlayer`, and `GothaMatch` dataclasses while preserving legacy mapping access.
- Handicap games (2026-08): stones-based handicap support landed end-to-end -- `category_service.handicap_points`/`suggested_handicap_stones` (OGS-inspired but using this app's own log category curve rather than a flat points-per-stone constant), auto-suggested/overridable handicap on manual and generated tournament pairings, OGS-style opponent-only rating shift in `rating_service.glicko2_update`, and handicap columns on `matches`/`tournament_pairings` with a defaulting migration. OpenGotha XML import (`import_gotha.py`, `import_service.py`, `tournament_service.create_tournament_from_gotha`) and the CSV importer both read an optional handicap value; missing/invalid values default to 0. Covered by `tests/test_handicap.py`.
- Date and round consistency: application-generated timestamps use UTC-5 by default or the active account's valid IANA timezone preference, and both rating calculation paths share deterministic date/round ordering. Invalid or unset preferences fall back to UTC-5.
- Timezone selector usability: account timezone selectors use one curated representative per current UTC offset, sort options ascending, display the UTC adjustment, and preserve raw IANA values for storage.
- Reporting: public reports default to All time, support player filtering and games-based selector ordering, and provide consistent CSV/PDF exports with localized PDF text and filter-preserving filenames.
- Tournament operations: draft tournaments are hidden from public listings, inactive players can be managed by administrators, and tournament actions support asynchronous panel refresh with redirect fallback for non-AJAX clients.

## 4. Low-priority / refactoring

- Review document cleanup: this file was shortened to a concise 4-section issue log; historical duplicate notes and stale long-form explanations were merged or removed.
- Optional cleanup: split large CSS bundles, keep dev-only scripts under `scripts/dev_only`, and trim backlog docs that drift from the active roadmap.
- Future follow-ups: scheduled backup retention, named reporting seasons, and broader observability improvements remain optional, non-blocking enhancements.

## Status summary

- Production blockers: none remaining in the current scope.
- Current project status: green, with the audit, auth, account profile/recovery, per-account timezone, round-order, tournament-delete modal, typed OpenGotha, reporting, and handicap-games changes completed and validated by 337 tests.

## 5. Remaining low-priority follow-ups

The project is green; the historical 4.x backlog has been narrowed down to items that still genuinely need work.

- Named reporting seasons and broader observability remain deferred.

---

## 6. Recommended order of work

The remaining work is narrow and optional rather than production-critical:

1. Add named reporting seasons if a real operational need emerges.

---

## 7. Files reviewed (with notes)

| File | Status | Notes |
|------|--------|-------|
| [app.py](app.py) | Reviewed | migration defaults, audit metadata, seed data |
| [config.py](config.py) | Reviewed | default rating constants, timezone, and SMTP settings |
| [routes/admin.py](routes/admin.py) | Reviewed | account profile and recovery routes, permissions, and rate limits |
| [services/category_service.py](services/category_service.py) | Reviewed | positive validation and updated_at persistence |
| [services/import_gotha.py](services/import_gotha.py) | Reviewed | typed tournament/participant/match payloads with legacy mapping access |
| [services/rating_service.py](services/rating_service.py) | Reviewed | dirty-date replay and transaction safety |
| [services/tournament_service.py](services/tournament_service.py) | Reviewed | typed OpenGotha metadata parser, pending-player cleanup, and export IDs |
| [templates/admin/tournaments.html](templates/admin/tournaments.html) | Reviewed | explicit tournament-delete modal |
| [templates/partials/matches_table.html](templates/partials/matches_table.html) | Reviewed | sort control UX follow-up |
| [tests/](tests) | Reviewed | regression coverage for fixed issues is present |
