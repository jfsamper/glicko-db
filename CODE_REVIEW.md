# Code Review – glicko-db

The original route and tournament-service decomposition has been verified for the nine targeted modules. The route migration and final service cleanup are complete. The other open items below are separate security, UX, and shared-module follow-ups.

## 0. 2026-09-14 re-verification pass

Every "resolved" item below was re-checked against the current codebase (not just re-read from this file). All hold up:

- `import_gotha_xml` is absent from the codebase; the active flow is still `build_import_preview()` → `create_tournament_from_gotha()`.
- `app.py`'s `if __name__ == "__main__":` block calls `app.run(host="0.0.0.0", port=5000)` with no `debug=True`.
- `routes/admin.py` is 588 lines (close to the ~575 previously recorded; the small drift is normal churn, not regrowth of the god-file). `services/tournament_service.py` is exactly 27 lines and contains only re-exports. The five tournament service modules and four `admin_*` route modules all exist and own the described responsibilities.
- `services/category_utils.py` still owns `format_glicko_category()`; `category_service.py` and `rating_service.py` both hold thin delegating wrappers only.
- `rating_service.glicko2_update()` still threads `tau` explicitly into `Player.update_player()`; no class-level mutable tau state.
- `legacy_players_integrity_state()`, `assert_legacy_players_state_clean()`, and `repair_legacy_players_table()` are all still in `app.py`; `scripts/check_legacy_players_state.py` still exists as the repeatable audit.
- `routes/sort_helpers.py` remains the single source for match/tournament sort fields and validators; `routes/admin.py` and `routes/public.py` import from it rather than redefining it.
- `services/i18n.py` owns `TRANSLATIONS`/`get_language`; `services/chart_service.py` owns chart construction; `services/common.py` only re-exports both for compatibility. Production route modules import the owners directly.
- `run_admin_db_action()` (`routes/admin.py`) is used by tournament participant/pairing/round-generation/deletion, user-deletion, and SGF unlink/delete handlers in `routes/admin_tournaments.py`, `routes/admin_sgf.py`, and `routes/admin_users.py`.
- No `_legacy_admin_*` route bodies or `_legacy()` service adapters remain anywhere in `routes/` or `services/`.
- The forgot-password route in `routes/admin_users.py` calls the same `record_failed_login_attempt()` IP limiter used by the login route, with the generic response preserved.
- `parse_handicap_stones()` (`routes/admin.py`) is used consistently by the manual add/edit match forms, the tournament pairing forms, and the CSV import path in `routes/admin_import.py`.
- Homepage stats render all three periods server-side with client-side tab switching (no reload); the "Noticias" card still ships literal `...` placeholder paragraphs in `templates/index.html` — **still open**, not actually resolved by any later change.
- Language switcher is still a single ES→EN→PT cycle button labeled with the *next* language — **still open**, as previously noted, low priority.
- `player.html` has no inline `style` attributes left; `category.html` keeps only the one data-driven `style="width: {{ pct }}%;"` bar, which is expected. `static/css/tournament.css` and `static/css/tables.css` exist and are used.
- Player-profile match/tournament history and report player-performance tables page locally over a preloaded dataset (no reload); rankings/players/matches/tournament lists still page server-side via `LIMIT ?/OFFSET ?`, consistent with the stated rationale.
- Public tournament round selection preloads all round pairings in the initial response and switches the visible round panel client-side without a form submission or page reload; standings remain tournament-wide.
- Full suite: **379 passed** (`pytest -q`; the additional coverage includes the public tournament round-preload/no-reload behavior).

No new correctness or security bugs were found during this pass. The two UX items below (News placeholder, language switcher) remain genuinely open and are carried forward unchanged.

## 1. Status check on previously-tracked issues
- import_gotha_xml() in import_service.py — resolved. The unused helper, its admin import, and its two obsolete tests were removed. The active XML flow remains build_import_preview() → create_tournament_from_gotha().

- Flask debug mode — resolved. The direct app.py entry point no longer passes debug=True; Passenger continues to call create_app() directly.

## 2. Architecture & maintainability
- The original god-file risk has been substantially reduced. `routes/admin.py` is now about 575 lines and primarily owns blueprint bootstrap, shared auth/DB helpers, and compatibility aliases; `services/tournament_service.py` is now a 27-line compatibility facade. Administrative ownership lives in `routes/admin_tournaments.py`, `routes/admin_matches.py`, `routes/admin_players.py`, and `routes/admin_users.py`; tournament ownership lives in `services/tournament_gotha.py`, `services/tournament_participants.py`, `services/tournament_pairing.py`, `services/tournament_matches.py`, and `services/tournament_standings.py`.

- Category/rating circular-import workaround — resolved. The pure formatter lives in `services/category_utils.py`, while `category_service.py` and `rating_service.py` retain thin compatibility wrappers for existing imports.

- Player tau propagation — resolved. `glicko2_update()` now passes tau explicitly through `Player.update_player()` into the volatility solver, so concurrent calculations no longer share mutable class-level state.

- Historical `players_corrupt` compatibility — verified clean, retirement pending as a separate change. `legacy_players_integrity_state()` and `assert_legacy_players_state_clean()` now check for the legacy table and child foreign keys, with the assertion running after startup repair. The read-only `scripts/check_legacy_players_state.py` audit checked the active database and seven managed backups on 2026-09-13: all eight had canonical `players`, no `players_corrupt` table, no child references, `PRAGMA integrity_check = ok`, and zero foreign-key violations. `repair_legacy_players_table()` remains isolated in `app.py` until a separate change removes or relocates the compatibility path.

- Shared route sort helpers — resolved. Match and tournament sort mappings and validators now live in `routes/sort_helpers.py`; `routes/admin.py` and `routes/public.py` re-export the same definitions, and the admin tournament query uses the shared `t.` SQL alias.

- Common-service ownership cleanup — resolved. `services/i18n.py` owns `TRANSLATIONS` and `get_language`; pure chart construction lives in `services/chart_service.py`; `services/db.py` owns the raw SQLite connection factory (`get_db()`); `services/timezone_service.py` owns configured-timezone resolution and all current-time/date helpers; `services/auth_service.py` owns user accounts, roles, sessions/permissions, and password-reset flows; `services/audit_service.py` owns the admin audit log schema/writes; `services/stats_service.py` owns the per-player games/wins/losses/draws recompute. `services/common.py` is now a pure compatibility facade re-exporting all of the above; production route/service modules import directly from the new owners, and `tests/test_service_ownership.py` asserts identity between the facade's re-exports and the owners' functions.

- Repetitive route boilerplate — resolved for uniform handlers. `routes/admin.py` provides `run_admin_db_action()` for the shared connection, rollback, translated validation errors, optional success flashes, and guaranteed close lifecycle. Tournament participant, pairing, round-generation, tournament-deletion, user-deletion, and SGF unlink/delete handlers use it. Import, SGF link, rating-refresh, recovery, and multi-stage handlers retain explicit lifecycles because they have distinct rollback or recovery behavior.

`services/tournament_service.py` is a 27-line compatibility facade that re-exports the established public and private import surface; it contains no tournament implementation bodies. No `_legacy_admin_*` route bodies or `_legacy()` service adapters remain. The last full-suite verification passed 379 tests.

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