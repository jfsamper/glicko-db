# Future roadmap and remaining work

This document lists only unfinished product, operational, and maintainability work. Completed import, audit, search, pagination, account profile/recovery, timezone-default, rating-order, and reporting work is summarized below rather than repeated in the backlog.

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
- Draft tournaments are hidden from public listings, while administrators can opt to show them in the tournament list.
- Administrators can activate or deactivate players; inactive players are excluded from rankings and tournament participant selection.
- Application-generated dates and times use UTC-5 by default; administrators can assign valid IANA timezones to individual accounts, and unset or invalid preferences fall back safely to UTC-5. Account selectors show one common representative per current UTC offset in ascending order, with Mexico, Colombia, Venezuela, Brazil, China, and South Korea preferred; South Korea represents the shared UTC+9 offset with Japan, and labels include the current adjustment while stored values remain IANA identifiers.
- Full rating recomputation and incremental replay process same-day matches by round, treating unknown rounds as round 1.
- Login rate-limit thresholds are environment-configurable through `config.py`, with defaults of five attempts in sixty seconds.
- Account profile and password recovery are available: users can save email, language, theme, timezone, and password at `/admin/profile`; recovery uses configurable SMTP, hashed expiring tokens, single-use consumption, and generic responses.
- Public reporting is available at `/reports` with inclusive calendar periods, All time defaults, player filtering, games-based selector ordering, opponent/country/club aggregates, and shared server-side totals.
- Reports can be exported as CSV or localized PDF; PDF exports include centered headings, the selected player and period in the filename, and preserve the active filters.
- Admin tournament actions support asynchronous panel refresh with redirect fallback for non-AJAX clients.
- Result moderation is available: members submit only for their linked player, and staff approve or reject before publication; hashed expiring approval-code helpers scaffold the future email flow.

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
- `services/common.py` and `routes/admin.py` build the curated timezone list, deduplicate it by current UTC offset, sort it ascending, and render UTC-adjusted labels without changing submitted IANA values.
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
- `templates/admin/profile.html` lets authenticated users change their email, default language, theme, timezone, and password; timezone options display their current UTC adjustment.
- `tests/test_profile_and_recovery.py` covers preference persistence, password changes, generic recovery responses, email delivery invocation, and token reuse rejection.
- SMTP delivery is configured through `MAIL_SERVER`, `MAIL_PORT`, `MAIL_USERNAME`, `MAIL_PASSWORD`, `MAIL_USE_TLS`, and `MAIL_FROM`.

### Result moderation workflow

Status: implemented and verified.

Confirmed in code:
- `services/common.py` adds the `member` role, nullable account-to-player linkage, the isolated `result_submissions` queue, and hashed expiring single-use approval-code helpers.
- `routes/admin.py` provides member registration and submission plus staff review, approval, rejection, audit logging, and rating refresh after approval.
- `templates/admin/report_results.html` and `templates/admin/result_submissions.html` provide the member and staff workflows.
- `tests/test_result_moderation.py` covers registration, member ownership restrictions, pending isolation, approval materialization, and legacy role migration.

The active workflow requires staff approval. Email delivery and automatic approval by code are intentionally scaffolded but disabled until the organization defines identity, recipient, and dispute-verification policy.

## Remaining implementation backlog

### P1 — Admin and platform

No remaining P1 items.

### P2 — Reporting and analytics

3. Reporting follow-ups
   - Add custom named seasons if the organization needs them beyond calendar periods

### P3 — Operational enhancements

4. Scheduled backups with retention policy
   - Disabled by default to avoid unnecessary server load
   - Only enable in production when there is a clear retention policy and maintenance schedule
   - Prefer inexpensive cron-style jobs with bounded execution windows and full validation

5. Replay and audit observability
   - Add clear operator feedback when rating replay is deferred
   - Expand operational summaries only where they support troubleshooting

### P4 — Workflow enhancements

6. Tournament preflight and dry-run approval workflow
   - Improve tournament and submission workflow
   - Evaluate need for pairings and BYE previews

## Technical implementation order

The login rate-limit settings, typed OpenGotha payload, explicit tournament-delete modal, per-account timezone preferences, account profile/recovery, date-bounded reporting, PDF reporting, and result moderation were completed and are no longer part of the remaining backlog. The next recommended sequence is:

1. Add named seasons if the reporting workflow requires them.
2. Add scheduled backups with retention and restore verification.
3. Consider replay observability and tournament preflight.

## Documentation rules

- Keep README.md, README.en.md, and README.pt.md synchronized.
- Link to this roadmap from each README and keep the summary brief.
- Keep legacy session flags out of current documentation and implementation.

