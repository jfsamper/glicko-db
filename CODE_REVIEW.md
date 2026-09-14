# Code Review – glicko-db

The original route and tournament-service decomposition has been verified for the nine targeted modules. The route migration and final service cleanup are complete. The other open items below are separate security, UX, and shared-module follow-ups.

## 1. Status check on previously-tracked issues
- import_gotha_xml() in import_service.py — resolved. The unused helper, its admin import, and its two obsolete tests were removed. The active XML flow remains build_import_preview() → create_tournament_from_gotha().

- Flask debug mode — resolved. The direct app.py entry point no longer passes debug=True; Passenger continues to call create_app() directly.

## 2. Architecture & maintainability
- The original god-file risk has been substantially reduced. `routes/admin.py` is now about 575 lines and primarily owns blueprint bootstrap, shared auth/DB helpers, and compatibility aliases; `services/tournament_service.py` is now a 27-line compatibility facade. Administrative ownership lives in `routes/admin_tournaments.py`, `routes/admin_matches.py`, `routes/admin_players.py`, and `routes/admin_users.py`; tournament ownership lives in `services/tournament_gotha.py`, `services/tournament_participants.py`, `services/tournament_pairing.py`, `services/tournament_matches.py`, and `services/tournament_standings.py`.

- Category/rating circular-import workaround — resolved. The pure formatter lives in `services/category_utils.py`, while `category_service.py` and `rating_service.py` retain thin compatibility wrappers for existing imports.

- Player._tau is a mutable class attribute set per-call in glicko2_update() (Player._tau = tau). This works fine under the app's current single-threaded-per-request SQLite usage, but it's a latent race condition if the app is ever run with threaded workers or concurrent background recomputation — one request's tau could leak into another's calculation mid-flight. Prefer an instance attribute or passing tau explicitly into the volatility solver.

- Historical `players_corrupt` compatibility — verified clean, retirement pending as a separate change. `legacy_players_integrity_state()` and `assert_legacy_players_state_clean()` now check for the legacy table and child foreign keys, with the assertion running after startup repair. The read-only `scripts/check_legacy_players_state.py` audit checked the active database and seven managed backups on 2026-09-13: all eight had canonical `players`, no `players_corrupt` table, no child references, `PRAGMA integrity_check = ok`, and zero foreign-key violations. `repair_legacy_players_table()` remains isolated in `app.py` until a separate change removes or relocates the compatibility path.

- Shared route sort helpers — resolved. Match and tournament sort mappings and validators now live in `routes/sort_helpers.py`; `routes/admin.py` and `routes/public.py` re-export the same definitions, and the admin tournament query uses the shared `t.` SQL alias.

- Common-service ownership cleanup — partially resolved by clear boundaries. `services/i18n.py` now owns `TRANSLATIONS` and `get_language`, while pure chart construction lives in `services/chart_service.py`; production consumers import those owners directly and `services/common.py` keeps compatibility re-exports. Auth, audit, timezone, database, and statistics helpers remain in `common.py` because their current connection/session/migration coupling does not provide a safe independent owner yet.

- Repetitive route boilerplate. Most admin POST handlers repeat the same conn = get_db(); try: ...; except ValueError as exc: flash(...); finally: conn.close() shape. A small helper/decorator for "run this DB action, flash a translated result" would cut a lot of near-duplicate code across admin.py.

`services/tournament_service.py` is a 27-line compatibility facade that re-exports the established public and private import surface; it contains no tournament implementation bodies. No `_legacy_admin_*` route bodies or `_legacy()` service adapters remain. The last full-suite verification passed 377 tests.

## 3. Security
Minor fixes:

- Password-reset request rate limiting — resolved. Forgot-password POST requests now use the same configurable IP-based limiter as failed logins before account lookup or email delivery; the generic response is unchanged.

- handicap_stones validation consistency — resolved. CSV imports now use `parse_handicap_stones`, so invalid values fail with the same 0–9 validation as match and tournament forms instead of being silently clamped.

## 4. Design / UX (live site)

- Homepage density — resolved. The statistics section renders all three already-computed periods in one response, shows All time by default, and switches All time, Year, and Quarter panels client-side through accessible tabs without another request or page reload.

- The "Noticias" (News) card ships literal placeholder content (... / ... in index.html) straight to production. Either wire it to something real or drop the section until there's content.

- Language switcher is a single-button cycle (ES→EN→PT) labeled with the next language's abbreviation rather than the current one — functional, but a first-time visitor has to experiment to understand it's a cycle rather than a static label. A small dropdown would be more discoverable, though this is a minor point given the audience is a known local club.

- Static inline layout styles — resolved for the reviewed profile/category surfaces. `player.html` and `category.html` now use reusable classes in `static/css/tournament.css`, and player table alignment is owned by `static/css/tables.css`; the category-bar percentage remains data-driven. Unrelated admin-template inline styles remain outside this focused cleanup.

- Client-side pagination — resolved where data is already preloaded. Player profile match/tournament history and report player-performance pagination now switch locally without requests or reloads. Rankings, players, matches, and tournament list pagination remain server-side because their filtered/sorted queries use `LIMIT/OFFSET` and should not preload the full result set.

## Implementation plan for remaining issues

1. Untangle the category/rating circular import — completed.
	- Moved the pure rating-to-category formatter into services/category_utils.py.
	- Kept thin compatibility wrappers in category_service.py and rating_service.py, so existing public imports remain stable.
	- Removed the bottom-of-file category_service.py import from rating_service.py; focused category, rating, handicap, and standings tests pass.

2. Split the largest route and service modules — completed.
	- Administrative ownership is split across the domain route modules, with endpoint names and compatibility imports preserved.
	- Tournament ownership is split across the five dedicated service modules listed above; `services/tournament_service.py` is a compatibility facade only.
	- The final dead `_legacy()` adapter was removed from `services/tournament_pairing.py`. Focused tournament/route tests and the full suite pass.

3. Reduce homepage density and remove placeholder news.
	- Present one statistics period at a time using the existing language and styling conventions, with a server-rendered default and accessible period navigation — completed with client-side switching, keyboard navigation, and default/invalid-period route coverage.
	- Remove the literal placeholder News card until a real data source exists.

4. Address the remaining low-severity consistency items.
	- Add the same rate limiter used for login attempts to password-reset requests without changing the generic response — completed.
	- Make CSV handicap validation use parse_handicap_stones so invalid values fail consistently instead of being clamped — completed and covered by a route-level regression test.
	- Consolidate duplicated match/tournament sort helpers into a shared module — completed and covered by shared-definition and route/list regression tests.
	- Split `TRANSLATIONS` and `get_language` out of `services/common.py` into `services/i18n.py`, and move pure chart helpers into `services/chart_service.py` — completed with compatibility re-exports and ownership tests. Auth, audit, timezone, database, and statistics extraction remains deferred until a cleaner ownership boundary is available.

5. Retire historical compatibility complexity after verification.
	- Document and verify the `players_corrupt` migration state across supported databases — completed for the active database and seven managed backups; the repeatable check is `scripts/check_legacy_players_state.py`.
	- Add a migration/integrity check proving no live database needs the repair path — completed with the startup assertion and `tests/test_critical_bug_fixes.py` coverage.
	- Remove or isolate `repair_legacy_players_table()` in a separate change after the clean-state verification has been observed across the deployment lifecycle.