# Future roadmap and remaining work

This document lists only unfinished product, operational, and maintainability work. Completed import, audit, search, pagination, account profile/recovery, timezone-default, and rating-order work is summarized below rather than repeated in the backlog.

## Current status

- Tournament pairing, standings, and import compatibility are stable for the main Swiss and McMahon flows.
- OpenGotha metadata handling is guarded against missing fields and malformed XML.
- The audit log is active for sensitive admin actions and is covered by regression tests.
- The app enforces named-account authorization for admin routes.
- Only `administrator` and `operator` roles can modify players, ratings, and categories; `tournament_director` is limited to tournament operations for those areas.
- The regression suite covers import correctness, rating replay safety, player filters, public-tournament routes, and the main tournament service paths.
- Admin tournament dashboard actions support progressive async panel refresh with redirect fallback.
- Tournament deletion uses an explicit confirmation modal instead of the browser `confirm()` dialog.
- Browser compatibility for older clients without `requestSubmit` is preserved.
- Search, sorting, and filtering behavior is now consistent across player, match, and tournament lists with page-state preservation for pagination.
- Backup restore now re-runs migrations, rebuilds the FTS5 search index, and restricts recovery candidates to managed backups plus the designated `.bak` fallback.
- Imported OpenGotha BYEs are persisted with participant history so pairing does not duplicate a BYE for a player who has already received one.
- OpenGotha metadata and match records are returned through typed `GothaTournamentPayload`, `GothaPlayer`, and `GothaMatch` dataclasses while preserving legacy mapping access for existing import consumers.
- Standings positions remain unique and sequential with deterministic tiebreak resolution.
- The dev-only pairing demo generator supports reproducible scenarios and includes Swiss/category variants.
- Application-generated dates and times use UTC-5 by default; administrators can assign valid IANA timezones to individual accounts, and unset or invalid preferences fall back safely to UTC-5.
- Full rating recomputation and incremental replay process same-day matches by round, treating unknown rounds as round 1.
- Login rate-limit thresholds are environment-configurable through `config.py`, with defaults of five attempts in sixty seconds.
- Account profile and password recovery are available: users can save email, language, theme, timezone, and password at `/admin/profile`; recovery uses configurable SMTP, hashed expiring tokens, single-use consumption, and generic responses.

## Verified implementation status

### Import preview and reconciliation report

Status: implemented and verified.

Confirmed in code:
- [services/import_service.py](services/import_service.py): `build_import_preview()` classifies exact, fuzzy, new, and duplicate rows and reports metadata conflicts.
- [routes/admin.py](routes/admin.py): `/admin/import` accepts explicit use-existing/create-new/reject decisions and editable metadata before commit.
- [templates/admin/import.html](templates/admin/import.html): preview UI shows reconciliation decisions, metadata editing, and reject/commit actions.
- [tests/test_critical_bug_fixes.py](tests/test_critical_bug_fixes.py): regression coverage verifies preview and explicit commit decisions.

### Admin audit log and review workflow

Status: implemented and verified.

Confirmed in code:
- [services/common.py](services/common.py): `migrate_audit_log_schema()` and `log_admin_action()` create and persist audit rows.
- [routes/admin.py](routes/admin.py): login, logout, category, rating, user lifecycle, and admin actions are logged; `/admin/audit` supports actor, action, text, and date filters.
- [templates/admin/audit.html](templates/admin/audit.html): review UI includes free-text and date-range filtering.
- [tests/test_security_and_app_factory.py](tests/test_security_and_app_factory.py): regression coverage verifies audit rows are created and reviewed.
- Successful state-changing admin actions are logged across tournament, match, player, rating, category, user, import, and backup workflows.
- Audit details are capped at 2 KiB per event and rows older than 730 days are pruned by default; `AUDIT_RETENTION_DAYS` overrides the retention period.

### Timezone preferences and round-aware rating replay

Status: implemented and verified.

Confirmed in code:
- `config.py` defines the fixed UTC-5 default timezone and the supported account timezone choices.
- `services/common.py` migrates the nullable `users.timezone` field, validates IANA timezone names, and resolves each active account's preference for application-generated dates and timestamps.
- `routes/admin.py` and the user-management templates allow administrators to create and edit account timezone preferences.
- `services/rating_service.py` orders both full recomputation and incremental replay by match date, normalized round number, and match id.
- Missing, zero, or invalid round values are treated as round 1; legacy match tables without a round column remain supported.
- Regression coverage verifies the UTC-5 fallback, account timezone resolution, invalid-value handling, account persistence, and same-day round ordering.

Historical note: older versions used a legacy `session["is_admin"]` compatibility marker. It has been removed; authorization now uses `user_id` and permission checks exclusively.

### Account profile and password recovery

Status: implemented and verified.

Confirmed in code:
- `services/common.py` migrates account email, language, and theme fields plus the password-reset token table; only token digests are stored.
- `routes/admin.py` provides `/admin/profile`, `/admin/forgot-password`, and `/admin/reset-password/<token>` with role checks, password verification, expiry, and one-time token use.
- `templates/admin/profile.html` lets authenticated users change their email, default language, theme, timezone, and password.
- `tests/test_profile_and_recovery.py` covers preference persistence, password changes, generic recovery responses, email delivery invocation, and token reuse rejection.
- SMTP delivery is configured through `MAIL_SERVER`, `MAIL_PORT`, `MAIL_USERNAME`, `MAIL_PASSWORD`, `MAIL_USE_TLS`, and `MAIL_FROM`.

## Remaining implementation backlog

### P1 — Admin and platform

1. Simple hidden/unpublished tournament status
   - Minimal `Hidden` or `Draft` flag on tournament entities
   - Optional public listing exclusion without full lifecycle management
   - Lower priority than audit/import work; no need for a full preflight workflow

2. Player activation and status management
   - Allow administrators to deactivate players
   - Define how inactive players affect rankings, imports, and tournament participation

### P2 — Reporting and analytics

3. Reporting follow-ups
   - Add custom named seasons if the organization needs them beyond calendar periods
   - Add PDF output only if CSV reports do not cover the operational workflow

### P3 — Operational enhancements

5. Scheduled backups with retention policy
   - Disabled by default to avoid unnecessary server load
   - Only enable in production when there is a clear retention policy and maintenance schedule
   - Prefer inexpensive cron-style jobs with bounded execution windows and full validation
   - Not a priority for the current roadmap

6. Result moderation workflow
   - Optional approval process for submitted tournament results
   - Requires a clear operational policy before implementation

7. Replay and audit observability
   - Add clear operator feedback when rating replay is deferred
   - Expand operational summaries only where they support troubleshooting

### P4 — Conditional enhancements

8. Tournament preflight and dry-run approval workflow
   - Not required for the current plan
   - Pairings and BYE previews are not necessary in the near term
   - Avoid unnecessary complexity unless a real workflow need appears

## Technical implementation order

The login rate-limit settings, typed OpenGotha payload, explicit tournament-delete modal, per-account timezone preferences, account profile/recovery, and date-bounded reporting were completed and are no longer part of the remaining backlog. The next recommended sequence is:

1. Add hidden/unpublished tournament visibility.
2. Add player activation/status management if operationally needed.
3. Add named seasons or PDF output only if the reporting workflow requires them.
4. Add scheduled backups with retention and restore verification.
5. Add result moderation only if the tournament workflow requires it.
6. Consider replay observability and tournament preflight after the core workflows are settled.

## Documentation rules

- Keep README.md, README.en.md, and README.pt.md synchronized.
- Link to this roadmap from each README and keep the summary brief.
- Keep legacy session flags out of current documentation and implementation.

## Current next steps

1. Evaluate named seasons and PDF output after using the date-bounded reports in production.
