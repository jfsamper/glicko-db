# Code Review v4 - glicko-db (2026-09-29)

## Implementation Follow-up (2026-09-29)

- **V4-01 fixed:** backup restore/delete are now administrator-only in the central map and mutation handlers; the operator route test covers all three backup POSTs.
- **V4-02 fixed:** recovery links use validated HTTPS `APP_PUBLIC_ORIGIN`, never request Host/forwarded headers. Unconfigured deployments do not issue recovery emails/tokens; root and prefixed links have tests.
- **V4-03 core failure fixed:** full XLSX replacement rejects missing/empty sheets and zero imported players or matches, runs as one rollback-capable transaction, and closes workbook handles. Preview, explicit confirmation, and verified backup remain follow-up work.
- **V4-04 fixed:** serialized user edit/delete checks preserve at least one active administrator; tests cover demotion, deactivation, deletion, inactive peers, and legitimate changes when a second active admin exists.
- Post-change suite: **393 passed, 1 known failure** in the unrelated generated help-link test (V4-11). All findings below describe the **pre-fix review snapshot**; V4-05 onward remain open.

## Findings

Priority: P1 = security, data integrity, or operational continuity; P2 = significant correctness or workflow failure; P3 = lower-impact maintenance. These are current-code findings, not proposed patches. Reproductions used isolated databases and disposable scripts that were removed afterward.

### V4-01 [P1] Operators can restore or delete backups

The permission map protects backup listing and creation, but omits restore and delete; its fallback is `operator` ([routes/admin.py](routes/admin.py#L507), [routes/admin.py](routes/admin.py#L580)). Both handlers repeat the `operator`-defaulting `admin_required()` check ([routes/admin_backups.py](routes/admin_backups.py#L101), [routes/admin_backups.py](routes/admin_backups.py#L138), [services/auth_service.py](services/auth_service.py#L582)). With an isolated operator account, GET `/admin/backups` and POST `/admin/backups/create` returned 403, while POST restore reached its file checks (302) and POST delete removed a real temporary backup (302). A hidden menu and CSRF token do not enforce administrator authorization. Require `admin` explicitly on **both** endpoints and inside both handlers; add role-by-route negative tests for every backup mutation. Do this before exposing backup operations through any other interface.

### V4-02 [P1] Password-reset email trusts an attacker-supplied Host

The forgot-password route creates an external URL from the incoming request ([routes/admin_users.py](routes/admin_users.py#L659)); the app has no configured trusted hosts or canonical external origin ([app.py](app.py#L43)). In an isolated request with `Host: attacker.invalid` and stubbed email delivery, the reset email URL had `attacker.invalid` as its host. A recipient following such a link can disclose a valid reset token to that host. Construct email links from a configured HTTPS public origin, reject untrusted hosts, and test alternate Host and reverse-proxy headers. Do not rely on the requester to select the reset-link domain.

### V4-03 [P1] A valid empty workbook destroys the existing dataset

The admin upload always uses `reset=True` ([routes/admin_import.py](routes/admin_import.py#L134)). The service deletes players, matches, and snapshots and **commits** before inspecting importable sheets ([services/import_service.py](services/import_service.py#L152), [services/import_service.py](services/import_service.py#L165)). A valid blank XLSX against an isolated database with two players and one match returned `{'players': 0, 'matches': 0}` and left both tables empty. Validate required sheets, headers, and nonempty records in a preview first; replace data in one rollback-capable transaction only after explicit confirmation and a verified backup. Add zero-row and mid-import-failure tests. Close the read-only workbook in `finally` as well: the probe's XLSX remained locked on Windows until garbage collection ([services/import_service.py](services/import_service.py#L165)).

### V4-04 [P1] The only administrator can remove their own admin access

User editing accepts a new role and active flag and replaces all role rows without checking the last active administrator ([routes/admin_users.py](routes/admin_users.py#L967), [routes/admin_users.py](routes/admin_users.py#L1004), [routes/admin_users.py](routes/admin_users.py#L1014)); deletion has no such guard either ([routes/admin_users.py](routes/admin_users.py#L1055)). With one admin and one member, the admin could demote itself: subsequent admin access was 403, active-admin count was zero, and bootstrap returned `None` because *some* users still existed ([services/auth_service.py](services/auth_service.py#L279)). Reject demotion, deactivation, or deletion of the last active administrator inside the same transaction, including concurrent edits; cover all three paths.

### V4-05 [P1] "Create new player" links the suggested existing player

The tournament form presents an empty ID as "Create new player" ([templates/admin/tournament.html](templates/admin/tournament.html#L114)). The route stores `resolved_player_id = NULL` without clearing `suggested_name` ([routes/admin_tournaments.py](routes/admin_tournaments.py#L409), [routes/admin_tournaments.py](routes/admin_tournaments.py#L440)); materialization interprets that NULL as permission to reuse the suggested player ([services/tournament_participants.py](services/tournament_participants.py#L223)). An isolated POST with the empty choice created **no** player and enrolled the suggestion instead. This can attribute tournament results to the wrong person. Preserve an explicit create-new decision, never auto-link an unconfirmed suggestion, and test suggested-link, different-player, create-new, and automatic materialization separately.

### V4-06 [P1] Automatic pairing ignores the tournament handicap switch

Manual pairing consults `tournament_handicap_enabled()`, but round generation unconditionally computes stones for every non-bye pairing ([services/tournament_pairing.py](services/tournament_pairing.py#L245), [services/tournament_pairing.py](services/tournament_pairing.py#L385)). A tournament with `handicap_enabled=0` produced a **9-stone** pairing when a stronger player was assigned White in an isolated test. Saved games then feed those stones into rating calculations. Gate automatic suggestions on the persisted switch, retain the existing explicit per-pair override, and test generation both before and after toggling the setting.

### V4-07 [P1] The login-attempt limit does not stop password guessing

Login authenticates first, clears attempts on success, and records/checks the limit only **after a failure** ([routes/admin_users.py](routes/admin_users.py#L79), [routes/admin_users.py](routes/admin_users.py#L101), [routes/admin.py](routes/admin.py#L299)). With a configured threshold of two, two wrong passwords returned 200 and the next correct guess was still admitted (302). The counter persists but does not block subsequent password checks. Enforce a pre-authentication limit per IP and account identifier with generic responses, an expiry window, and concurrency-safe updates; test that another guess is not evaluated after the threshold. The separate forgot-password precheck reported fixed by v3 is still present.

### V4-08 [P2] Restored older backups lack the moderation table

Normal startup runs result-submission and audit migrations ([app.py](app.py#L919), [app.py](app.py#L921)); restore runs auth/news/other migrations but omits those two ([services/backup_service.py](services/backup_service.py#L201), [services/backup_service.py](services/backup_service.py#L214)). Restoring a minimal older backup into an isolated database recreated `users` but not `result_submissions`; an immediate authenticated GET of `/admin/report-results` raised `OperationalError: no such table: result_submissions`. The audit table is also absent after the service call, though the backup *route's* later audit write may create it. Use one shared complete migration sequence for startup and restore, then exercise every feature immediately after restoring representative real backup schemas.

### V4-09 [P2] A failed refresh resubmits a successful tournament POST

After a successful mutation, the dashboard awaits a separate refresh GET inside the same `try`; any refresh error falls into `catch { form.submit(); }` ([static/js/admin_tournament_async.js](static/js/admin_tournament_async.js#L39), [static/js/admin_tournament_async.js](static/js/admin_tournament_async.js#L96), [static/js/admin_tournament_async.js](static/js/admin_tournament_async.js#L108)). An isolated JS harness observed one successful fetch POST followed by a native form resubmission when GET failed. The affected forms include round generation ([templates/admin/tournament.html](templates/admin/tournament.html#L324)); generating successive rounds without results is supported by current tests ([tests/test_tournament_service.py](tests/test_tournament_service.py#L694)), so a duplicate can create an extra round. Never retry a possibly committed POST to recover a failed GET. Show a refresh error/reload option and add server-side idempotency for non-repeatable commands.

### V4-10 [P2] Password reset leaves old authenticated sessions usable

Sessions contain a user ID, and every request checks only that the account still exists and is active ([services/auth_service.py](services/auth_service.py#L369)). Reset changes the password hash but not an account/session epoch ([services/auth_service.py](services/auth_service.py#L517), [services/auth_service.py](services/auth_service.py#L543)). After an isolated reset, the previously logged-in client still received 200 for `/admin/users`. If a session was compromised, changing the password does not evict it. Track an auth version or post-reset timestamp in the session and invalidate other sessions and future API tokens on password change, deactivation, or restore.

### V4-11 [P2] Clean checkouts lack help HTML and OpenGotha test fixtures

The app serves prebuilt HTML files directly ([routes/public.py](routes/public.py#L223)), but their entire output directory is ignored ([.gitignore](.gitignore#L78)); `git ls-files docs/generated` is empty. A fresh checkout therefore lacks the files unless a separate build runs. This local output was built with a `/glicko` prefix, while root-mounted routes serve it unchanged; the full suite fails the expected root `href="/help/readme?lang=es"` assertion ([tests/test_public_tournaments.py](tests/test_public_tournaments.py#L139)). Several OpenGotha tests also read sample XML from ignored `uploads/` ([tests/test_tournament_service.py](tests/test_tournament_service.py#L47), [tests/test_opengotha_comparison.py](tests/test_opengotha_comparison.py#L18), [.gitignore](.gitignore#L75)); no upload files are tracked. A clean clone cannot run those tests without local data. Make guide generation a required build/deployment and test prerequisite, handle mount prefixes consistently, and move sanitized XML examples into tracked test fixtures rather than runtime uploads. Test both root and prefixed installations from a clean checkout.

### V4-12 [P2] CSV header normalization accepts rows the importer cannot read

CSV validation lowercases and trims header *names*, then uses the original `DictReader` row keys with lowercase lookups ([routes/admin_import.py](routes/admin_import.py#L163), [routes/admin_import.py](routes/admin_import.py#L181)). `Date,White,Black,Result` passes validation, but `row.get("white")` is `None`, so all games are silently skipped and the route reports success with zero matches. Canonicalize field names before reading rows (and reject ambiguous duplicates) or require exact names; test case/whitespace variants and actual imported counts.

### V4-13 [P2] Same-day snapshots hide rating change in one-day reports

Report dates are inclusive, but the starting snapshot includes entries **on** the first date ([services/reporting_service.py](services/reporting_service.py#L182)). A single snapshot that changes a player from 1500 to 1530 on the only selected day returns start=1530, end=1530, change=0; the UI/CSV/PDF share this calculation. An existing multi-day test explicitly expects start-day snapshots to be the baseline ([tests/test_reporting_service.py](tests/test_reporting_service.py#L156)), so settle the product definition before changing it. If "change over the selected period" includes the first day's games, use the last snapshot strictly before that day (or initial rating) and update the test; otherwise relabel the metric to describe the post-start-day baseline.

### V4-14 [P3] Player export assumes an ignored directory exists

This fixes v3's root-level CSV concern but introduces a clean-checkout failure: the script writes into ignored `exports/` without creating it ([scripts/export_players.py](scripts/export_players.py#L13), [scripts/export_players.py](scripts/export_players.py#L32), [.gitignore](.gitignore#L76)). The directory is absent in this checkout. Create `OUTPUT_FILE.parent` before opening and test the command with a valid temporary DB and no pre-existing output directory.

## v3 Recheck

| v3 item | Current verdict | Evidence / remaining work |
| --- | --- | --- |
| 1.1 root-level player CSV | Resolved, with V4-14 follow-up | Output moved under ignored `exports/` ([scripts/export_players.py](scripts/export_players.py#L13)). No root CSV is tracked here. |
| 1.2 double-closing rating connection | Resolved | The owning connection is closed once, in `finally` ([services/rating_service.py](services/rating_service.py#L149)). |
| 1.3 duplicate `unpair` translation keys | Resolved | Each of es/en/pt has one `unpair` entry ([services/i18n.py](services/i18n.py#L360), [services/i18n.py](services/i18n.py#L887), [services/i18n.py](services/i18n.py#L1411)); an AST scan found no duplicate literal keys in any translation map. |
| 1.4 OpenGotha triple XML parse | Resolved | Import parses once and passes that root to metadata extraction ([services/tournament_gotha.py](services/tournament_gotha.py#L172)). |
| 1.5 dense tournament modules | Still a maintenance concern | Pairing, participant, and OpenGotha functions still mix long statements and commits; reformat only alongside behavior changes ([services/tournament_pairing.py](services/tournament_pairing.py#L222), [services/tournament_participants.py](services/tournament_participants.py#L192)). |
| 1.6 large admin route hub | Still a maintenance concern | Blueprint, permission fallback, helpers, and route aliases share one module ([routes/admin.py](routes/admin.py#L507)); make access policy explicit before more routes or an API are added. |
| 1.7 deprecated OpenGotha comparison module/test | Resolved | The module and its self-referential deprecation test are absent from this checkout. |

The v3 "already fixed" claims still hold for Glicko-2 `_f()` ([services/glicko2.py](services/glicko2.py#L136)), the removed dead import function, no debug run flag ([app.py](app.py#L1155)), one-way category/rating imports, explicit `tau` passing, pre-lookup forgot-password throttling ([routes/admin_users.py](routes/admin_users.py#L641)), and CSV handicap validation. V4-07 concerns **login**, not a reversal of the forgot-password fix. The dead black-result template branch noted by v3 remains ([templates/admin/tournament.html](templates/admin/tournament.html#L242)); it is cosmetic. Its self-service registration idea remains useful, with the draft-visibility decision below.

## Broader Review Notes

- **What is working:** Named accounts, role checks, production CSRF, secure cookie defaults, allowlisted sort fields, parameterized data filters, bounded SGF reads, and numerical Glicko regression tests provide a good foundation ([app.py](app.py#L48), [services/sgf_service.py](services/sgf_service.py#L11), [tests/test_glicko2_f_correctness.py](tests/test_glicko2_f_correctness.py)). These controls do not repair the authorization and transaction gaps above.
- **Backup reliability:** Creation copies a live SQLite file with `shutil.copy2`, and restore overwrites the live database before migrations complete ([routes/admin_backups.py](routes/admin_backups.py#L80), [services/backup_service.py](services/backup_service.py#L163)). This is a concurrency/recovery risk, not a corruption reproduced in this review. Prefer SQLite's connection backup API, verify the staged copy and SGF sidecar, migrate it, then replace atomically with rollback and a restore generation that invalidates sessions. The existing roadmap already calls for scheduled backups and restore verification ([FUTURE_FEATURES.md](FUTURE_FEATURES.md#L138)).
- **Transaction ownership:** Pairing and status helpers call `commit()` inside caller-owned connections ([services/tournament_pairing.py](services/tournament_pairing.py#L260), [services/tournament_status.py](services/tournament_status.py#L90)). This makes multi-step operations hard to roll back as one unit. Centralize commits at the route/use-case boundary before adding API writes, and make rating replay failure states visible.
- **Public performance:** `/reports` builds all match/player metrics and then slices the player list for pagination ([services/reporting_service.py](services/reporting_service.py#L237), [routes/public.py](routes/public.py#L390)); home stats read snapshots per player for three periods ([services/home_stats.py](services/home_stats.py#L61), [services/home_stats.py](services/home_stats.py#L149)). No large-data benchmark was run. Add representative-volume timing/query-count tests, then use filtered SQL/aggregation or cached summaries rather than doing more Python work per page or API request.
- **Draft policy needs a decision:** Anonymous visitors are offered "Show drafts" and can request draft details ([templates/tournaments.html](templates/tournaments.html#L81), [routes/public.py](routes/public.py#L875), [routes/public.py](routes/public.py#L1004)); existing tests deliberately assert this ([tests/test_public_tournaments.py](tests/test_public_tournaments.py#L28)). If drafts are unannounced/private, gate **both** list and detail; if they are public previews, say so consistently in the README and guides. Do not treat `status='draft'` as an API privacy boundary until that policy is set.
- **Usability and operations:** A workbook preview/dry run, skipped-row counts, size limits for workbook/CSV/XML uploads, explicit restore confirmation, self-hosted versioned UI dependencies, and pre-release mobile/keyboard checks would lower support costs. Honor reduced-motion preferences for menu/hover transitions as well ([static/css/layout.css](static/css/layout.css#L647)). The current admin import saves uploads before validation ([routes/admin_import.py](routes/admin_import.py#L116)), and base layout loads third-party slider assets on every page ([templates/base.html](templates/base.html#L13)). These are recommendations, not separately reproduced defects.

## Feature Suggestions

1. **Self-service tournament registration** (carried forward from v3): member accounts linked to players can request a spot on a *published, registration-open* tournament, with a director-set close time/capacity, idempotent enrollment, waitlist/withdrawal rules, and staff approval for unmatched accounts. Keep unpublished draft policy separate from registration state.
2. **Import safety workflow:** Show a validated preview with row-level errors, affected-player/match counts, reconciliation choices, and a backup snapshot before destructive replacement; require a second confirmation and provide an audit/recovery receipt.
3. **Operational visibility:** Schedule verified backups only after restore is safe; surface dirty-rating replay state, last successful replay, backup health, and failed administrative actions without exposing secrets to members.
4. **Report scalability and clarity:** Fix or relabel first-day rating change, measure report/home response time against a larger fixture, and add named seasons only if the organization needs them (as the current roadmap suggests).

## Plan: A Real Auth-Only API

The current integration guide explicitly states there is **no stable REST API**; public HTML, CSV/PDF exports, and AJAX redirect JSON are not an API contract ([docs/api_endpoints.md](docs/api_endpoints.md#L14)). Keep public web pages public. Every route under a new `/api/v1` blueprint, including read-only resources and its OpenAPI document, must require an active authenticated identity; do not quietly expose an anonymous JSON mirror of the site.

### 1. Prerequisites and identity

- Close V4-01 through V4-08, particularly backup authorization, Host validation, actual login throttling, last-admin protection, explicit reconciliation, handicap correctness, and consistent restore migrations. Define whether draft tournaments are confidential. Stabilize transaction boundaries and report query costs before exposing write/high-volume read endpoints.
- Use **opaque bearer personal-access tokens**, not the browser's ambient session cookie or a shared `ADMIN_PASSWORD`. Issue them through the existing authenticated, CSRF-protected profile/admin UI after password confirmation; no anonymous `/api/v1/token` bootstrap endpoint. Store only a digest and metadata (`user_id`, allowed scopes, expiry, revoked time, name, last use); show the raw token once. Require HTTPS, short default lifetime, rotation/revocation, per-token/IP rate limits, redacted logs, and no cross-origin browser access by default.
- Resolve the user and current role(s) on **every** request; deny missing/expired/revoked tokens, inactive users, and unknown scopes. Never serialize `password_hash` from the current auth payload ([services/auth_service.py](services/auth_service.py#L369)). Revoke tokens and browser sessions on password reset, deactivation, role change where scopes shrink, or DB restore. For APIs, return JSON 401 (missing/invalid token) vs 403 (valid identity, insufficient scope); keep existing web login redirects and CSRF behavior unchanged.

### 2. Contract and permission matrix

| API resource | `member` | `operator` | `tournament_director` | `administrator` |
| --- | --- | --- | --- | --- |
| Players, rankings, matches, published tournaments/standings, reports | Read with token | Read | Read | Read |
| Own profile and result submissions | Own records; create pending results only for linked player | Own records | Own records | Own records |
| Review submissions, match/news/SGF operations | No | Yes | Yes, where the current web policy allows | Yes |
| Tournament participants, pairings, rounds, results | No | Yes | Yes | Yes |
| Users, roles, settings, audit | No | No | No | Yes |
| Backup create/restore/delete | Not in initial API | Not in initial API | Not in initial API | Defer until an audited, atomic design |

Use named permissions/scopes shared with web policy, but remove the admin map's permissive fallback for API routes. An account with multiple role rows needs an explicit union/priority policy: the current lookup chooses only the alphabetically first role ([services/auth_service.py](services/auth_service.py#L401)). Member IDs and linked-player ownership must be derived from the token's user, never a client-supplied `submitted_by_user_id`. Decide whether an authenticated member can view draft data separately from staff scope.

### 3. Incremental endpoint delivery

1. **Read-only v1:** `GET /api/v1/me`, `/players`, `/players/{id}`, `/matches`, `/tournaments`, `/tournaments/{id}`, `/tournaments/{id}/standings`, `/reports`; add SGF metadata/download only with explicit authenticated streaming and size rules. Reuse domain services, not HTML handlers. SQL-backed cursor or bounded page pagination (maximum 100), validated filters/sort keys, stable IDs and ordering, ISO-8601 dates, documented nullability, and no private user/email fields in public-style resources.
2. **Member workflow:** `GET/POST /api/v1/me/result-submissions`, reusing the moderation queue and server-derived linked player, with 201/Location and duplicate detection. Staff review/decision endpoints come next with explicit operator permission, audit events, optimistic concurrency, and idempotency keys. Keep proposed results out of ratings until approval.
3. **Tournament writes:** Add participant/round/pairing/result commands only after V4-05/06/09 and transaction refactoring. Validate status transitions, reject ambiguous repeat commands, use `Idempotency-Key` on round generation/imports, and recompute/mark ratings once per committed change. Do not ship direct rating or database-restore writes in v1.

Publish a versioned OpenAPI 3.1 schema and authenticated interactive docs, typed request/response examples for es/en/pt clients, JSON validation errors (`application/problem+json`), standard 400/401/403/404/409/422/429 behavior, and clear compatibility/deprecation rules. Keep all three integration guides and READMEs synchronized when API behavior actually ships, not in this review-only pass.

### 4. Gates and rollout

- Contract tests for **every** `/api/v1` GET and mutation with no token, wrong role, expired/revoked token, inactive account, and wrong owner; assert no HTML redirects and no accidental cookie-only access. Cover token issuance/revocation, rate limits, restore/role/password changes, CSRF on issuance UI, and absent CORS permissions.
- Fixture tests for filtering/pagination, inclusive dates, tournament and moderation invariants, optimistic concurrency and repeated idempotency keys; run against fresh schema and representative real-backup migrations. Document a safe migration/rollback path and feature-flag staff writers during initial rollout.
- Ship read endpoints first behind authentication, observe latency/audit/error rates, then enable member submissions and staff writes per role. Never use the current AJAX redirect payloads or HTML form routes as a public contract.

## Verification and Limits

- Full suite in this local checkout: **377 passed, 1 failed** (`tests/test_public_tournaments.py::test_help_route_serves_the_localized_interface_guide`), with the root-link assertion described in V4-11. A fresh clone is additionally missing the ignored XML fixtures; it was not run here. The prior README's passing-test count is not a current verification result.
- Isolated throwaway probes confirmed backup deletion by an operator, reset-link Host poisoning, login after the attempt threshold, surviving session after reset, last-admin demotion, empty-workbook data loss, missing restored tables, wrong pending-player linkage, ignored handicap flag, start-day report calculation, CSV header behavior, and duplicate AJAX submission. No production database, external email, or vendor folder was tested or modified.
- This was a source-and-test review, not a production penetration test, accessibility audit, restore-under-concurrent-write test, or large-dataset benchmark. The performance and backup-copy notes above are labeled as risks until measured. All one-off scripts were deleted; existing review-file worktree changes were left untouched.
