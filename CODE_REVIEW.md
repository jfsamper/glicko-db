# Code Review – glicko-db

The main risks now are maintainability (a few files have grown very large) and a handful of loose ends that the [previous review](CODE_REVIEW..old.md) calls "green" but that are still visibly open in the source.

## 1. Status check on previously-tracked issues
- import_gotha_xml() in import_service.py — resolved. The unused helper, its admin import, and its two obsolete tests were removed. The active XML flow remains build_import_preview() → create_tournament_from_gotha().

- Flask debug mode — resolved. The direct app.py entry point no longer passes debug=True; Passenger continues to call create_app() directly.

## 2. Architecture & maintainability
- routes/admin.py (~2,800 lines) and services/tournament_service.py (~2,000 lines) have become "god files." Both mix several distinct concerns (auth/session handling, match CRUD, tournament CRUD, backups, OpenGotha XML import/export, pairing orchestration). This is the biggest structural risk in the repo — not because anything's broken, but because every change to one concern risks touching unrelated code. Splitting admin.py into per-domain blueprints (admin_matches.py, admin_tournaments.py, admin_users.py, admin_backups.py) and splitting tournament_service.py into gotha_import.py / gotha_export.py / pairing_orchestration.py would pay off.

- Circular-import workaround is self-documented but still fragile. category_service.py imports glicko_to_category from rating_service at the bottom of the file specifically to dodge a circular import, with a comment already flagging this as awkward. It works today, but it's one reordered import away from breaking. The suggested fix (pull glicko_to_category into a small shared module neither service depends on) is worth actually doing.

- Player._tau is a mutable class attribute set per-call in glicko2_update() (Player._tau = tau). This works fine under the app's current single-threaded-per-request SQLite usage, but it's a latent race condition if the app is ever run with threaded workers or concurrent background recomputation — one request's tau could leak into another's calculation mid-flight. Prefer an instance attribute or passing tau explicitly into the volatility solver.

- repair_legacy_players_table() / the players_corrupt handling in app.py is defensive code for what looks like a real historical incident (a table literally renamed to players_corrupt during some past debugging session, plus dangling FKs pointing at it). It's good that it's handled robustly and tested, but it's also permanent complexity that now runs on every tournament create/delete. Worth understanding root cause well enough to be confident it can't recur, and maybe scheduling removal of this compatibility shim after a verified clean migration.

- Duplicated sort/filter helpers. routes/admin.py redefines _parse_match_sort, _parse_match_order, and TOURNAMENT_SORT_FIELDS with logic identical to routes/public.py (while separately importing _match_filter_sql and parse_tournament_sort from public). Consolidating these into one shared module would remove the risk of the two copies drifting apart.

- services/common.py is a kitchen-sink module: auth, audit logging, timezone helpers, chart-building math, and the entire multilingual TRANSLATIONS dict (well over 1,000 lines) all live in one file. Splitting TRANSLATIONS out to its own i18n.py (or per-locale JSON) would make translation edits low-risk and separate from the security-sensitive auth code sitting in the same file.

- Repetitive route boilerplate. Most admin POST handlers repeat the same conn = get_db(); try: ...; except ValueError as exc: flash(...); finally: conn.close() shape. A small helper/decorator for "run this DB action, flash a translated result" would cut a lot of near-duplicate code across admin.py.

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

2. Split the largest route and service modules by ownership.
	- Extract routes/admin.py into match, tournament, user/profile, and backup/import modules while preserving endpoint names and the existing blueprint registration contract.
	- Extract services/tournament_service.py into OpenGotha import/export and pairing orchestration modules, keeping compatibility wrappers only where callers still need them.
	- Move one domain at a time, run its focused tests, then run the full suite and verify url_for() endpoint resolution.

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