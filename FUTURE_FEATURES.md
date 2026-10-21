# Project roadmap

This is the single home for proposed product, integration, and operational work, including the API plan and Feature Suggestions moved from [Code Review v4](CODE_REVIEW.v4.md). Priorities are proposed, not release commitments. Nothing described as planned below is an implemented endpoint or feature.

Current route behavior is documented in the [English integration guide](docs/api_endpoints.en.md), with [Spanish](docs/api_endpoints.md) and [Portuguese](docs/api_endpoints.pt.md) equivalents. The application remains server-rendered; there is no stable REST API today.

## Contents

- [Current foundations and constraints](#current-foundations-and-constraints)
- [Authenticated API migration](#authenticated-api-migration)
  - [A0 - Inventory and baseline](#a0---inventory-and-baseline)
  - [A1 - Contract and authorization](#a1---contract-and-authorization)
  - [A2 - Token lifecycle and API infrastructure](#a2---token-lifecycle-and-api-infrastructure)
  - [A3 - Read-only API](#a3---read-only-api)
  - [A4 - Member submissions and moderation](#a4---member-submissions-and-moderation)
  - [A5 - Tournament commands and staff writes](#a5---tournament-commands-and-staff-writes)
  - [A6 - Rollout, compatibility, and operations](#a6---rollout-compatibility-and-operations)
- [Product and operational backlog](#product-and-operational-backlog)
- [Documentation and maintenance](#documentation-and-maintenance)

## Current foundations and constraints

Do not reopen historical findings or reimplement existing infrastructure merely because the original review listed them as prerequisites:

| Foundation | Evidence and planning implication |
| --- | --- |
| Numbered v4 findings | V4-01 through V4-14 are recorded as fixed in the [review follow-up](CODE_REVIEW.v4.md#implementation-follow-up-2026-09-29). Preserve their regressions; the historical 410-test checkpoint is not a current validation result. |
| Named roles and session invalidation | [Authentication service](services/auth_service.py) loads all roles, grants permissions through any held role, and validates browser `auth_version`. Reuse that policy; do not reproduce the review's obsolete alphabetical-first-role behavior. Bearer-token lifecycle support is still planned. |
| Command transactions | [Transaction helpers](services/transactions.py) and [rollback tests](tests/test_transaction_boundaries.py) already defer nested helper commits. Verify each API use case joins one command boundary rather than adding another transaction framework. |
| Idempotency | [Idempotency service](services/idempotency_service.py) and [tournament tests](tests/test_tournament_idempotency.py) provide an existing foundation. Extend it for API payload fingerprints, JSON outcome replay, and concurrent retries where needed. |
| Backup and restore | [Backup service](services/backup_service.py) already uses verified online SQLite snapshots and staged migration before replacement; [scheduled-backup tests](tests/test_scheduled_backup.py) cover verified scheduled backups and retention. Operational rollout and concurrent-write restore drills remain separate work. Current restore leaves SGF files unchanged and clears missing-file links; do not promise the review's proposed SGF sidecar restoration. |
| Visibility and ratings | Drafts are staff-only in the documented web workflow; canceled tournaments are excluded from the public list. Public matches/profiles hide draft/canceled tournament games; only completed linked tournament games and standalone games count toward ratings. Preserve these rules and verify list, detail, nested resources, and downloads separately. |
| Member moderation | Submitted results remain pending and must not change public matches, ratings, or reports until approved. Reuse the existing moderation workflow. |

## Authenticated API migration

### Target and non-goals

- Add a versioned `/api/v1` interface alongside the existing Flask web application, not a replacement UI, SPA rewrite, or database-engine migration.
- Keep public HTML pages and existing CSV/PDF exports available with their current access rules. Do not convert existing web paths or AJAX redirect payloads into API contracts.
- Require an active bearer-token identity for **every** API route, including read resources, SGF downloads, OpenAPI, and interactive documentation. No anonymous JSON mirror, cookie-only access, password-grant token endpoint, or shared bootstrap password.
- Use thin API adapters over shared services, explicit serializers, and common named permissions. Services own domain behavior; adapters own JSON, HTTP headers/status, and request validation.
- Defer direct rating edits/replay commands, full-dataset replacement, and database backup/restore/delete endpoints beyond initial v1. Existing administrator web workflows remain the supported path.

### Delivery order

| Priority | Milestone | Depends on | Deliverable / release gate |
| --- | --- | --- | --- |
| P0 | A0 | None | Verified route/service inventory, invariant coverage, and measured baseline |
| P0 | A1 | A0 | Approved OpenAPI contract, scope/permission matrix, and compatibility policy |
| P0 | A2 | A1 | Token issuance/revocation, deny-by-default API boundary, migrations, and lifecycle tests |
| P1 | A3 | A2 | Authenticated read-only pilot with parity and bounded-query tests |
| P2 | A4 | A3 | Owner-scoped pending submissions, then audited staff moderation |
| P3 | A5 | A4 and per-command transaction/idempotency checks | Gated tournament commands, followed by separately approved staff resources |
| P0* | A6 | Staged after A3, A4, and A5 | Observed rollout and a verified disable/rollback path for each release |

P0 means a prerequisite to exposing an API slice; P1 is the first value-bearing pilot after those prerequisites; P2/P3 defer progressively more consequential and complex writes. P0* makes A6 a mandatory release gate for each slice, not work that must precede A3. No timeline is assigned until A0 identifies the effort and a maintainer owns each milestone.

### A0 - Inventory and baseline

1. Inventory public, member, and administrative route groups from the integration guide against current handlers, shared services, permission names, filters, exports, and tests. Record which handlers still contain reusable domain logic and extract only what the selected API slice needs.
2. Preserve fixed review behavior: last-active-admin protection, canonical recovery origin, login throttling, restore migrations/session invalidation, explicit participant reconciliation, handicap settings, safe imports, and inclusive report-date baselines.
3. Verify draft/canceled visibility across details, pairings, profiles, standings, SGF metadata/downloads, and alternate identifiers. An authenticated member is not staff; authentication alone must not reveal drafts.
4. Trace each planned write through its transaction, audit, rating-dirty marker, and idempotency boundary. Identify remaining helper commits and external side effects before exposing that command.
5. Measure reports, home statistics, rankings, and planned list queries on a reproducible larger fixture. Record dataset size, hardware, query counts, p50/p95 latency, and SQLite lock behavior. Agree numeric endpoint latency/query budgets before the read pilot; optimize filtered SQL/aggregation or summaries only where measurements justify it.

**Exit:** a resource-to-service/permission/test inventory, reproducible measurements, and explicit blockers for each delivery slice. Existing fixed findings are regression gates, not new open bugs.

#### Initial A0 findings and provisional priorities

This is the first static pass, not A0 completion. The handler-by-handler inventory, invariant matrix, representative-volume fixture, and measurements below remain outstanding.

| Finding | Current evidence | Planning consequence |
| --- | --- | --- |
| There is no implemented `/api/v1` or bearer-token route. | The integration guide describes the app as server-rendered; public handlers are in `routes/public.py`, while member and staff workflows are in the admin route modules. | A1 and A2 are P0: freeze the contract and authorization model, then build the token boundary before exposing any resource. Do not treat web sessions or AJAX JSON redirects as API authentication/contracts. |
| Useful read logic is partly reusable, but the web adapter owns filtering and presentation in places. | Existing service entry points include `load_player` / `load_rankings` (`services/player_service.py`), `build_date_report` (`services/reporting_service.py`), and `get_tournament_standings` (`services/tournament_standings.py`). Public handlers combine these with request parsing and HTML rendering. | A3 is P1 after A2. Map each proposed operation to a service and test first; extract only domain/query logic needed by the selected slice rather than serializing templates or copying route code. |
| Role permissions already union all roles, but the web route map has a permissive fallback and session-coupled checks. | `services/auth_service.py` provides named permission checks; `routes/admin.py` defaults unmapped endpoints to `operator`; `can_view_draft_tournaments()` reads the current web identity. | A1/A2 are P0. API operations must map explicitly to named permissions and a bearer principal; never inherit the admin route fallback or let a token scope elevate a role. |
| Draft/canceled visibility logic is shared for matches and SGF, but it is not yet a complete API resource policy. | `services/public_visibility.py` centralizes match filtering and hidden SGF filenames. `tests/test_draft_visibility.py` covers match visibility, SGF library/downloads, and news tags, but does not establish parity for every proposed detail, pairing, participant, standings, profile, or alternate-ID operation. | A3 is P1 only after A0 closes the resource-by-resource visibility matrix. Member authentication must not expose staff-only drafts. |
| Write foundations exist but do not yet establish API command semantics. | `services/transactions.py` provides a shared command boundary; `services/idempotency_service.py` stores tournament command status/location, not a fingerprinted/replayable API response body. | A4 is P2 and A5 P3. Trace each command's side effects and transaction boundary; extend idempotency for API payloads/concurrency where required before enabling writes. |
| API performance readiness is unknown. | Existing tests cover ranking filters and report behavior, but no API workload, representative larger fixture, or numeric latency/query budget is recorded. | Keep A0 P0 and block A3's performance exit until the fixture and measurements exist. Do not prioritize optimization without measured hotspots. |

The priorities apply to API milestones only. The separate product/operational backlog items already marked `next` are unchanged; this pass does not reprioritize them.

### A1 - Contract and authorization

Write and review a versioned OpenAPI 3.1 contract before implementing client integrations. The endpoint names below are proposed, not currently callable.

#### Resource delivery map

All paths below are relative to `/api/v1`; every request requires a token and the relevant scope.

| Slice | Proposed methods and paths | Boundary |
| --- | --- | --- |
| A3: identity | `GET /me` | Explicit safe fields for the caller; never serialize the internal auth payload |
| A3: players and rankings | `GET /players`, `/players/{id}`, `/players/{id}/matches`, `/players/{id}/rating-history`, `/rankings` | Public-style player data only; bounded histories and allowlisted filters |
| A3: matches | `GET /matches`, `/matches/{id}` | Apply tournament visibility before selecting/serializing records |
| A3: tournaments | `GET /tournaments`, `/tournaments/{id}`, `/tournaments/{id}/participants`, `/tournaments/{id}/pairings`, `/tournaments/{id}/standings` | Draft data requires the existing staff permission as well as scope |
| A3: reports | `GET /reports` | Inclusive date filters and existing pre-start rating baseline; expose rating freshness |
| A3 follow-up: content | `GET /categories`, `/news`, `/news/{id}`, `/sgf-records`, `/sgf-records/{id}`, `/sgf-records/{id}/download` | Published news only; approve SGF identifiers, access checks, streaming, and size limits first |
| A4: member results | `GET/POST /me/result-submissions`, `GET /me/result-submissions/{id}` | Caller-owned records; creation only for the linked player, initially pending |
| A4: moderation | `GET /result-submissions`, `GET /result-submissions/{id}`, `POST /result-submissions/{id}/approve`, `/result-submissions/{id}/reject` | Current staff moderation permission; audited, conditional, idempotent decisions |
| A5: tournaments | `POST /tournaments`; `PATCH /tournaments/{id}`; `POST/DELETE /tournaments/{id}/participants/{player_id}`; `POST /tournaments/{id}/rounds`; `POST /tournaments/{id}/rounds/{round_id}/process`; `PATCH /tournaments/{id}/pairings/{pairing_id}/result` | Per-operation web permission, validated transitions, and one atomic command |
| A5 follow-up: staff resources | Player/match CRUD, news publishing, SGF linking/deletion, bounded batch actions, and non-destructive imports | Specify methods/schemas individually after the first tournament write pilot; no blanket admin proxy |
| Beyond initial v1 | Users/roles, application settings, audit access, backup/restore/delete, destructive replacement, rating commands | Separate scope review and operational safety gate; no implied initial API availability |

Existing CSV/PDF exports remain supported; adding authenticated API export/download variants requires a separate documented schema, media type, and resource budget, not a redirect to public or administrative handlers.

#### Permission and scope rules

Effective access is the intersection of token scopes, the user's **current union of named-role permissions**, ownership, resource visibility, and valid domain state. A scope never elevates a role. API operations and unknown scopes/permissions must fail closed without relying on any web permission fallback.

| Resource/action | Member | Operator | Tournament director | Administrator |
| --- | --- | --- | --- | --- |
| Public-style reads with read scopes | Yes | Yes | Yes | Yes |
| Draft tournament reads | No | Only as current web policy permits | Only as current web policy permits | Yes |
| Own profile / own pending results | Own records | Own records | Own records | Own records |
| Moderation, match/news/SGF writes | No | Per named web permission | Per named web permission, not all data-admin actions | Yes |
| Tournament configuration / participants / rounds | No | Per-operation policy; do not grant all tournament administration | Per named web permission | Yes |
| Users / roles / settings / audit | No | No | No | Administrator-only if delivered later |
| Backup / restore / direct rating writes | Not in initial v1 | Not in initial v1 | Not in initial v1 | Not in initial v1 |

Define named scopes such as `players:read`, `matches:read`, `tournaments:read`, `reports:read`, `results:submit`, `results:review`, and `tournaments:write`, with narrower write scopes where the operation requires them. Map every operation to an explicit existing permission; add finer-grained permissions to shared policy only when needed by both surfaces. Derive user and linked-player ownership server-side, never from `submitted_by_user_id` or a supplied owner.

#### Wire contract

- Explicit schemas for IDs, Glicko rating/RD/volatility, result enums, status transitions, optional/null fields, and read-only versus writable properties. Reject unknown write fields and invalid filter/sort values.
- JSON success objects use `{"data": {...}}`; collections use `{"data": [], "pagination": {"page": 1, "per_page": 25, "has_next": false}}`. Default `per_page=25`, maximum 100; reject larger/invalid values. Use stable allowlisted ordering with an ID tie-breaker. Introduce cursors only if baseline results justify them, before freezing that resource's contract.
- Preserve documented country/region/group, player, rating/activity, and report date filters where relevant. Define combined group-filter semantics explicitly. Dates use `YYYY-MM-DD`; timestamps use ISO-8601 UTC with `Z`. Date ranges remain inclusive.
- Use `application/problem+json` with `type`, `title`, `status`, `detail`, `instance`, stable machine `code`, request ID, and field errors when relevant. Never return login redirects, flashed HTML, traceback details, or a success-shaped empty fallback.
- Specify 200 reads/updates, 201 plus `Location` for creation, and 204 for deletion; 400 malformed input, 401 missing/invalid bearer credentials with `WWW-Authenticate`, 403 insufficient scope/permission, 404 missing or concealed resources, 405 unsupported method, 409 domain/idempotency conflict, 412 stale `If-Match`, 413 oversized request, 415 unsupported media type, 422 schema errors, 428 missing required precondition, 429 with `Retry-After`, and sanitized 500/503 failures.
- Define ETag/revision and `If-Match` requirements for conflicting edits/moderation, request/upload limits, authentication/rate-limit headers, and safe retry rules. No state-changing GETs.
- Freeze machine field names/error codes across languages. Provide typed request/response examples and es/en/pt integration instructions; localized display strings must not become enum values.
- Document version/deprecation rules: additive optional fields may remain in v1; breaking field, permission, or semantic changes require explicit migration/versioning. Never reuse removed IDs or silently reinterpret result values.

**Exit:** every first-release operation has schemas, examples, permissions/scopes, pagination, errors, and negative test cases; deferred resources are explicitly excluded.

### A2 - Token lifecycle and API infrastructure

1. Add additive token-schema migrations to the shared startup/restore migration sequence. Store only a cryptographic digest, lookup identifier, user ID, name, allowed scopes, created/expiry/revoked/last-use timestamps, and issuance auth version. Generate high-entropy opaque personal-access tokens; show the raw value once.
2. Issue/list/revoke tokens through the existing authenticated profile/admin UI after password confirmation, with CSRF protection. Owners manage their tokens; administrators can revoke according to an explicit permission. Propose a 30-day default lifetime and 90-day maximum for maintainer approval in A1; require expiry and rotation, with no permanent shared token.
3. Accept credentials only in `Authorization: Bearer`, over HTTPS in deployment. Reject URL/form tokens and cookie-only identity. Resolve the user, active state, current roles, token scopes, expiry, and revocation on every request without serializing password hashes or reset tokens.
4. Bind token validity to account changes: invalidate on password change/reset, deactivation, and privilege reduction; never automatically expand scopes on promotion. Reuse auth-version hooks while preserving existing browser-session UX.
5. Handle restore rollback explicitly: a database-only auth-version increment can collide with a token version from a newer database. Revoke **all restored token rows** during staged restore, and test that pre-restore credentials cannot authenticate after replacement. Re-enable issuance only after restore completes.
6. Introduce a dedicated API blueprint, serializers, schema validation, and JSON error translation. Scope any CSRF exemption strictly to bearer-only API routes; retain CSRF on all existing forms and token-management UI.
7. Enforce auth at the API namespace boundary, including unknown paths and unsupported methods, with JSON routing errors after authentication. Flask blueprint handlers alone cannot handle routing 404s; use narrowly scoped application-level handling without changing web errors/redirects. Preserve status and required headers. See [Flask error-handling guidance](https://flask.palletsprojects.com/en/stable/errorhandling/).
8. Add persistent/concurrency-safe per-token and per-IP rate limits, stricter write limits, request IDs, structured redacted logs, and audit identity. No CORS permission by default; authenticated interactive docs must not persist or leak tokens. Include downloads and schema/docs assets in the access review.

**Exit:** tests cover missing/malformed/expired/revoked tokens, cookie-only requests, inactive/deleted users, role unions/reductions, wrong scopes, CSRF issuance, rate limits, restore, JSON 404/405/500, secret redaction, and absence of cross-origin permissions.

### A3 - Read-only API

1. Deliver `/me`, player/ranking, match, published tournament, standings, and report reads first. Add nested histories/lists only with bounded database queries; do not render HTML and scrape it back into JSON.
2. Use safe field allowlists. Public-style resources must omit account email, password hashes, tokens, private moderation data, audit internals, and unpublished news. `/me` is separately owner-scoped.
3. Match web filters, date baselines, draft/canceled visibility, and completed-tournament rating eligibility. Include a documented freshness object for reports/rankings when ratings are dirty, rather than implying recalculation already happened.
4. Apply limits in SQL where possible before materialization; validate page boundaries, stable ordering, empty results, nulls, invalid dates, and combined filters. Authenticated responses must not leak through shared caches.
5. Add SGF/content reads only after filename traversal, stable identifiers, content types/disposition, missing files, bounded streaming, and inherited match/tournament visibility are tested. Never redirect an authenticated download to an unchecked file route.

**Exit:** parity/contract tests pass, every list is bounded, agreed A0 latency/query budgets are met, and no anonymous or member request can retrieve staff-only data. A read-only staff pilot may then begin.

### A4 - Member submissions and moderation

1. Add owner-scoped list/detail/create operations using the existing submission service. Resolve the linked player from the token user; reject unlinked or unrelated-player submissions through documented errors.
2. Return 201 with `Location` for a new pending submission; detect duplicates and define replay versus conflict behavior. Require an idempotency key for creation. Pending records must not enter public match/report/rating queries.
3. Deliver staff queue/approve/reject operations only with explicit shared permissions, result-validation rules, audit events, and revision preconditions. Recheck ownership/permissions/state inside the write transaction.
4. Use one transaction for the moderation decision, resulting match, rating-dirty marker, audit event, and persisted idempotency outcome. Concurrent or repeated approval must never create two matches.

**Exit:** wrong-owner, forged-owner, unlinked-player, duplicate, concurrent-decision, stale-revision, and failed-transaction tests pass; approved results affect visibility/ratings exactly as in the web workflow.

### A5 - Tournament commands and staff writes

1. Implement one command family at a time: tournament configuration, participant enrollment/removal, round generation, pairing results, then round processing. Preserve per-operation permissions rather than granting operators unrestricted tournament configuration.
2. Reuse reconciliation decisions, handicap settings, BYE/absence rules, tournament visibility/status transitions, and all-or-nothing batch limits. Reject invalid transitions and ambiguous retries. Require `Idempotency-Key` for non-repeatable creation/processing/import/batch commands.
3. Extend the existing idempotency store to bind keys to user, operation, resource, and canonical payload fingerprint. Persist the HTTP status/body/location needed for JSON replay; identical completed retries replay, changed payloads conflict, and concurrent attempts cannot both execute. Recheck current authorization before replay. Define pending/failure recovery and retention/retry windows in the contract.
4. Commit the command's domain changes, rating invalidation, audit event, and outcome once. Simulate failure at each step, including between commit and response delivery; retry must not create a second round/match or reapply an import.
5. Use revisions/conditional requests for edits and state-dependent commands. Mark the earliest rating-dirty date once per committed change; expose freshness and failures, not an implicit successful replay.
6. Then specify separate player/match CRUD, news, SGF, and non-destructive import slices. Imports need bounded uploads, row diagnostics, preview, explicit confirmation where appropriate, and staged-file cleanup; destructive replacement remains deferred. Defer long-running execution until its resource budget and any job/status contract are reviewed.

**Exit:** route-by-role/scope tests, concurrent retries, stale writes, rollback, audit, rating invalidation, and batch-limit tests pass for every enabled command. No direct restore or rating mutation is accidentally exposed.

### A6 - Rollout, compatibility, and operations

- Add independently controlled read/member/staff API switches without changing existing web access. Start disabled; pilot reads with staff tokens, then members, submissions, moderation, and individual staff command families.
- Run contract tests for every API method, including OpenAPI/docs/downloads: missing credentials, cookie-only access, expired/revoked credentials, inactive accounts, insufficient scope/role, and wrong owner. Compare registered operations against the contract to catch accidentally unguarded routes.
- Test fresh and representative legacy-backup migrations, token revocation on restore, root and prefixed deployments, pagination/filter invariants, es/en/pt examples, and unchanged web login/CSRF/CSV/PDF behavior. Run the narrowest relevant suites per change, then `pytest -q` before each release.
- Track per-operation latency, lock contention, rate-limit/auth failures, sanitized errors, audit outcomes, rating freshness, and backup health. Agree alert/rollback thresholds from A0 measurements before enabling each slice.
- Document deployment HTTPS/proxy settings, issuance/rotation/revocation, rate/upload limits, safe retries, restore behavior, and incident response. Provide a smoke-test client against disposable data, not production credentials.
- Verify rollback by disabling API slices and revoking pilot tokens while preserving committed domain data and the existing web app. Keep migrations additive; do not blindly restore an older database to undo an API deployment or claim that disabling endpoints reverses writes.
- Publish release notes and compatibility/deprecation notices. Update current-behavior documentation only when the corresponding slice actually ships.

**Exit:** signed-off contract/security/correctness tests, measured budgets, a successful disable/rollback drill, and synchronized documentation for the released slice.

## Product and operational backlog

The following merges the review's Feature Suggestions with the previous roadmap. These can improve the web application independently of API delivery; an API is not a prerequisite for all product work.

| ID / priority | Proposal | Dependencies and acceptance criteria |
| --- | --- | --- |
| F1 / next | Import safety follow-up | Preserve implemented XLSX preview, explicit confirmation, and verified pre-replacement backup. Assess current CSV/XLSX/OpenGotha diagnostics; add missing row/field errors, upload/decompression/row limits, and a recovery receipt containing counts, warnings, backup reference, and rating-dirty state, without secrets. Test over-limit input, malformed data, partial failure, and cleanup. Reuse for A5 imports. |
| F2 / next | Operational visibility and restore drills | Build on existing verified scheduled backups, retention, and staged restores, not a new scheduler. Document deployment scheduling and test restoration under realistic concurrent access in disposable environments. Surface dirty-rating earliest date, last successful replay, replay failures, last verified backup/restore check, retention/backup failures, and failed admin actions. Restrict operational details to staff; public-style responses expose only safe freshness. Clarify that database restore does not restore SGF files and document their separate protection. |
| F3 / next | Report/home scalability and clarity | Share A0's representative-volume fixture, timings, query counts, and agreed budgets. Optimize measured hotspots using filtered SQL/aggregation or invalidated summaries; preserve the resolved V4-13 inclusive-period baseline in HTML/CSV/PDF/API. Add named reporting seasons only as optional in addition to calendar periods, with explicit boundaries/timezone semantics. |
| F4 / later | Non-rated tournaments | Show eligible tournaments and games in profiles/match history without affecting ratings. Specify the interaction between rated flag and draft/active/completed/canceled status, and test transitions/replay/report consistency across web and API. |
| F5 / later | PairGo tournaments | Define team composition, pair identity, pairings, results, profiles, and whether/how ratings apply before changing the singles data model. Preserve existing singles systems. |
| F6 / later | Country- and region-scoped permissions | Add explicit resource scope to existing named permissions; define cross-region players/tournaments and administrator override. Test list filtering and direct-ID reads/writes on web and API. Token scopes must not bypass geographic restrictions. |
| F7 / later | UI resilience and accessibility | Evaluate self-hosted versioned UI dependencies, reduced-motion behavior, mobile layout, keyboard navigation, and focus/error handling before release. Preserve current language/theme behavior; verify actual workflows rather than only screenshots. |
| F8 / explore | Tournament preflight and approval dry run | Preview participant reconciliation, settings, expected operations, warnings, and rating impact without mutation; require explicit approval and detect changes between preview and execution. Reuse established transaction/import-preview patterns. |
| F9 / explore | Self-service tournament registration | Linked members request a place only on a published, registration-open tournament. Define director-controlled closing time/timezone, capacity, idempotent enrollment, waitlist promotion, withdrawal/cancellation rules, and staff approval for unmatched accounts. Registration state must be separate from draft visibility. Test concurrent requests for the last place. Add API registration only after this domain workflow exists. |

## Documentation and maintenance

- Keep this roadmap as the canonical plan; the review retains historical findings and verification limits, and integration guides describe only shipped behavior with a link here.
- Keep Spanish, English, and Portuguese READMEs and integration guides aligned. Preserve public-web-versus-authenticated-API distinctions; label examples for unshipped routes as proposals.
- At A1 select minimal maintained schema/OpenAPI validation tooling compatible with the project's Python minimum; do not add a second web framework or duplicate the shared policy/transaction layers without demonstrated need.
- Extract admin access helpers only where the API/web adapters need a shared service or the admin module's growth warrants it. Reformat dense tournament code alongside relevant behavior changes, not as an unrelated rewrite.
- Record milestone completion with the exact tests/measurements and released contract version. Move completed proposals to release history rather than treating historical review notes as open work.
