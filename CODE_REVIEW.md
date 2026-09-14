# Code Review – glicko-db

The original route and tournament-service decomposition has been verified for the nine targeted modules. The route migration and final service cleanup are complete. The other open items below are separate security, UX, and shared-module follow-ups.

## 1. Status check on previously-tracked issues
- import_gotha_xml() in import_service.py — resolved. The unused helper, its admin import, and its two obsolete tests were removed. The active XML flow remains build_import_preview() → create_tournament_from_gotha().

- Flask debug mode — resolved. The direct app.py entry point no longer passes debug=True; Passenger continues to call create_app() directly.

## 2. Architecture & maintainability
- The original god-file risk has been substantially reduced. `routes/admin.py` is now about 600 lines and primarily owns blueprint bootstrap, shared auth/DB helpers, and compatibility aliases; `services/tournament_service.py` is now a 27-line compatibility facade. The domain implementations live in the extracted modules listed in the migration checklist below.

- Category/rating circular-import workaround — resolved. The pure formatter lives in `services/category_utils.py`, while `category_service.py` and `rating_service.py` retain thin compatibility wrappers for existing imports.

- Player._tau is a mutable class attribute set per-call in glicko2_update() (Player._tau = tau). This works fine under the app's current single-threaded-per-request SQLite usage, but it's a latent race condition if the app is ever run with threaded workers or concurrent background recomputation — one request's tau could leak into another's calculation mid-flight. Prefer an instance attribute or passing tau explicitly into the volatility solver.

- repair_legacy_players_table() / the players_corrupt handling in app.py is defensive code for what looks like a real historical incident (a table literally renamed to players_corrupt during some past debugging session, plus dangling FKs pointing at it). It's good that it's handled robustly and tested, but it's also permanent complexity that now runs on every tournament create/delete. Worth understanding root cause well enough to be confident it can't recur, and maybe scheduling removal of this compatibility shim after a verified clean migration.

- Duplicated sort/filter helpers. routes/admin.py redefines _parse_match_sort, _parse_match_order, and TOURNAMENT_SORT_FIELDS with logic identical to routes/public.py (while separately importing _match_filter_sql and parse_tournament_sort from public). Consolidating these into one shared module would remove the risk of the two copies drifting apart.

- services/common.py is a kitchen-sink module: auth, audit logging, timezone helpers, chart-building math, and the entire multilingual TRANSLATIONS dict (well over 1,000 lines) all live in one file. Splitting TRANSLATIONS out to its own i18n.py (or per-locale JSON) would make translation edits low-risk and separate from the security-sensitive auth code sitting in the same file.

- Repetitive route boilerplate. Most admin POST handlers repeat the same conn = get_db(); try: ...; except ValueError as exc: flash(...); finally: conn.close() shape. A small helper/decorator for "run this DB action, flash a translated result" would cut a lot of near-duplicate code across admin.py.

### Issue-2 migration verification

The originally targeted modules were checked against current blueprint registration, function ownership, and compatibility imports:

| Target module | Verified ownership | Remaining caveat |
|------|------|------|
| [routes/admin_tournaments.py](routes/admin_tournaments.py) | Tournament listing, creation, settings, detail, participant, pairing, result, round, import, export, and unpair handlers | Uses `routes.admin` only as the shared helper/compatibility boundary; all tournament endpoints register directly to local handlers. |
| [routes/admin_matches.py](routes/admin_matches.py) | Match listing, creation, editing, deletion, SGF handling, pagination, and SQLite recovery | Uses `routes.admin` only as the shared helper/compatibility boundary. |
| [routes/admin_players.py](routes/admin_players.py) | Player rankings/CRUD, category configuration, and rating configuration/recalculation | Uses `routes.admin` only as the shared helper/compatibility boundary. |
| [routes/admin_users.py](routes/admin_users.py) | Authentication, registration, result reporting/moderation, profiles, settings, password recovery, logout, audit, and user administration | Directly registers its view functions; shared helper access remains through `routes.admin`. |
| [services/tournament_gotha.py](services/tournament_gotha.py) | OpenGotha metadata parsing, tournament creation persistence, and XML export | None in the production ownership path. |
| [services/tournament_participants.py](services/tournament_participants.py) | Participant lookup/listing, pending-player materialization, name reconciliation, and McMahon seed persistence | None in the production ownership path. |
| [services/tournament_pairing.py](services/tournament_pairing.py) | Pairing policy, round generation, BYE/status handling, participant mutations, pairing edits, and handicap updates | None in the production ownership path. |
| [services/tournament_matches.py](services/tournament_matches.py) | Pairing-to-match synchronization, result updates, tournament-wide match sync/save, and round result materialization | None in the production ownership path. |
| [services/tournament_standings.py](services/tournament_standings.py) | Tournament-specific standings assembly | None in the production ownership path. |

`services/tournament_service.py` is a 27-line compatibility facade that re-exports the established public and private import surface; it contains no tournament implementation bodies. No `_legacy_admin_*` route bodies or `_legacy()` service adapters remain. The last full-suite verification passed 370 tests.

## 3. Security
Minor fixes:

- Password-reset requests aren't rate-limited (only login attempts are), so the endpoint could be used to spam the configured SMTP account. Low severity given the generic response, but easy to add the same limiter.

- handicap_stones validation is inconsistent between entry points: the match/tournament forms reject out-of-range values with a hard error (parse_handicap_stones, update_pairing_handicap), while the CSV importer silently clamps to 0–9. Not a security issue, just a UX inconsistency worth aligning.

## 4. Design / UX (live site)

- Homepage is very dense. It renders all three time periods (All-time / Year / Quarter) × five metrics × five entries each = 75 rows on first load. A tabbed or accordion view (similar to the season dropdown already used on /reports) would reduce clutter and page weight without losing information.

- The "Noticias" (News) card ships literal placeholder content (... / ... in index.html) straight to production. Either wire it to something real or drop the section until there's content.

- Language switcher is a single-button cycle (ES→EN→PT) labeled with the next language's abbreviation rather than the current one — functional, but a first-time visitor has to experiment to understand it's a cycle rather than a static label. A small dropdown would be more discoverable, though this is a minor point given the audience is a known local club.

- Inline style="" attributes are scattered through several templates (player.html, category.html) for layout (grid/gap/text-align) rather than color — this doesn't fight the dark theme, but it does undercut the otherwise clean CSS-variable-driven theming approach; moving these into tournament.css/tables.css classes would make future theme edits easier.

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
	- Present one statistics period at a time using the existing language and styling conventions, with a server-rendered default and accessible period navigation.
	- Remove the literal placeholder News card until a real data source exists.
	- Add route/template tests for the default period, period switching, and absence of placeholder content.

4. Address the remaining low-severity consistency items.
	- Add the same rate limiter used for login attempts to password-reset requests without changing the generic response.
	- Make CSV handicap validation use parse_handicap_stones so invalid values fail consistently instead of being clamped.
	- Consolidate duplicated match/tournament sort helpers into a shared module.
	- Split TRANSLATIONS out of services/common.py, then extract the remaining common concerns only where ownership is clear.

5. Retire historical compatibility complexity after verification.
	- Document and verify the players_corrupt migration state across supported databases.
	- Add a migration/integrity check proving no live database needs the repair path, then remove or isolate repair_legacy_players_table() in a separate change.