# Code Review Follow-up – glicko-db (2026-09-14)

This pass re-verifies every previously-tracked issue directly against the source provided, rather than trusting prior review notes or `CODE_REVIEW.md`'s own summary. Where the two disagreed, the actual code wins.

## 0. Good news: several previously "open" items are actually fixed

Independent of what any changelog says, the source in front of me shows these are resolved:

- **`services/glicko2.py` `Player._f()`** uses `self.__rd**2` (phi, rating deviation) in both the numerator and denominator, matching Glickman's published formula. The `self.__rating`/`self.__rd` mix-up that was flagged in earlier notes is not present in this snapshot. `tests/test_glicko2_f_correctness.py` pins this with a direct unit test, the Glickman worked example, and a from-scratch reference-implementation cross-check across four scenarios — good regression coverage for a subtle numerical bug.
- **`import_gotha_xml()` dead code**: there is no `import_gotha_xml` function anywhere in `services/import_service.py` or `services/import_gotha.py`. The active path is `parse_gotha_xml()` → `build_import_preview()` → `create_tournament_from_gotha()`, consistent with the documented flow. Nothing to remove here.
- **Flask debug mode**: `app.py`'s `if __name__ == "__main__":` block calls `app.run(host="0.0.0.0", port=5000)` with no `debug=True`. The `REMOVE BELOW FROM PROD` comment above it is stale — there's nothing under it to remove.
- **Category/rating circular import**: `services/category_service.py` only imports from `category_utils`, `db`, and `timezone_service`; `services/rating_service.py` imports from `category_service` one-way. No cycle.
- **`Player._tau` mutable class state**: `tau` is threaded explicitly as a parameter through `update_player()` → `_newVol()` → `_f()`, not stored as shared class/instance state. Concurrent calls with different `tau` values won't clobber each other.
- **Password-reset rate limiting**: `admin_forgot_password()` calls the same `record_failed_login_attempt()` IP limiter used by login, before any account lookup or email send.
- **Handicap validation consistency**: `parse_handicap_stones()` is used uniformly by the match add/edit forms, tournament pairing forms, and the CSV import path (`routes/admin_import.py`), so an invalid CSV handicap value now rejects the row instead of silently clamping.

If you're keeping a tracking doc, I'd mark all of the above closed and drop them from the backlog — carrying them forward as "still open" would just be noise at this point.

## 1. Remaining issues

### 1.1 `player_categories.csv` is still committed and not gitignored
`scripts/export_players.py` writes to `ROOT / "player_categories.csv"` — i.e. the repository root, not `data/`, `uploads/`, or `exports/`. `.gitignore` covers `data/`, `uploads/`, `exports/`, and `backups/*.{zip,tar,gz,bak,sql}`, but has no rule for a bare root-level `player_categories.csv`. If this file gets committed by accident it'll silently drift out of sync with the live database (and it's already a full player list with ratings — data you probably don't want checked into git history). Add a line:
```
/player_categories.csv
```

### 1.2 `rating_service.recompute_ratings()` has redundant/confusing lifecycle code
```python
try:
    ...
    if owns_conn:
        conn.commit()
    clear_dirty_date(conn=conn)
    if owns_conn:
        conn.close()
finally:
    if owns_conn:
        conn.close()
```
This closes the connection once inside the `try` block and again in `finally`. SQLite tolerates a double-close, so it's not a functional bug, but it's exactly the kind of thing that becomes a real bug the next time someone edits this function without noticing the duplication. Drop the inner `if owns_conn: conn.close()` and let `finally` own it exclusively.

### 1.3 Duplicate dictionary keys in `services/i18n.py`
Each language dict in `TRANSLATIONS` defines `"unpair"` twice (and there may be others — I only spot-checked). Python silently keeps the last value, so there's no runtime bug, but it's a maintenance trap: a future edit to the first occurrence will look like it did nothing. Worth a quick `python -c "..."` pass or linter rule to catch repeated keys in these large literal dicts.

### 1.4 `create_tournament_from_gotha()` parses the XML file three times
`services/tournament_gotha.py::create_tournament_from_gotha()` calls `read_gotha_tournament(xml_path)` (which does its own `ET.parse`), then does a second `ET.parse(xml_path).getroot()` just to check for any non-zero `handicap` attribute, then a third `ET.parse(xml_path).getroot()` for the actual `Games` loop. For typical club tournament sizes this is harmless, but for a large multi-day XML it's wasted I/O and parsing. Parse once and reuse the `root` for all three passes.

### 1.5 Large, densely single-lined service modules
`services/tournament_pairing.py`, `services/tournament_gotha.py`, and `services/tournament_participants.py` contain many multi-statement one-liner functions (several `;`-separated statements per line, deeply nested ternaries). They're logically sound as far as I can tell, but this style makes diffs and code review meaningfully harder — a bug introduced in one of these lines would be easy to miss. Not urgent, but worth reformatting opportunistically when these files are next touched for a real change (not as a standalone refactor PR, to avoid a huge diff for no functional gain).

### 1.6 `admin.py` is still the largest route module
Per the file provided, `routes/admin.py` is still doing a lot: blueprint bootstrap, ~15 shared helpers, permission maps, menu sections, and several inline route handlers, even after the `admin_*` submodule split. It's better than a true god-file, but it's still the first place anyone has to look when touching admin behavior. If more admin functionality is added, consider moving `ADMIN_MENU_SECTIONS`/`ADMIN_ROUTE_PERMISSIONS` and the login-attempt helpers into a dedicated `services/admin_access.py` (pure data + no Flask imports), leaving `admin.py` for blueprint wiring only.

### 1.7 Legacy `gotha2glicko.py` is deprecated but still load-bearing for one test
`gotha2glicko.py` is marked deprecated in its own docstring and only exists to keep `tests/test_opengotha_comparison.py::test_gotha2glicko_warns_and_handles_missing_games` passing. If nothing outside the test suite calls it anymore, consider retiring both the module and that one test together — keeping a "deprecated" module alive purely to test its own deprecation warning is circular.

## 2. Smaller comments (not bugs, just noticed while reading)

- `templates/admin/tournament.html`'s black-result button builds its `result` hidden input with:
  ```jinja
  value="{% if pairing.result == '0-1' %}{% endif %}{% if pairing.result != '0-1' %}0-1{% endif %}"
  ```
  The first `{% if %}` branch is always empty, so this is equivalent to `{% if pairing.result != '0-1' %}0-1{% endif %}`. Functionally fine (it produces the intended toggle-off-if-already-selected behavior), but the dead branch reads like a leftover from an earlier edit. Simplifying it would remove a moment of "wait, why is this here?" for the next reader.
- `services/tournament_gotha.py::export_tournament_results()` and `create_tournament_from_gotha()` are both single giant functions (100+ lines). They work, and they're covered by tests, but if OpenGotha's XML dialect ever needs another special case, this is the place a bug is likeliest to hide.
- `config.py` reads `ADMIN_PASSWORD = os.environ["ADMIN_PASSWORD"]` at import time — correct for "fail loudly if unset," but it means importing `config` in a throwaway script or REPL requires the env var to be set even for read-only inspection. Not worth changing (it's an intentional safety choice), just noting for anyone confused by an unexpected `KeyError` in a one-off script.

## 3. Suggested future feature: self-service tournament registration

Right now, tournament rosters are entirely admin-managed: participants are added one at a time via `/admin/tournaments/<id>/players`, and there's no path for a member to sign themselves up for an announced tournament. Given the app already has:

- a `member` role and account linkage (`users.player_id`),
- a public tournament list (`/tournaments`) that already distinguishes draft vs. published tournaments, and
- pending-player infrastructure (`tournament_pending_players`) for reconciling names,

a natural next feature is **self-service registration**: a logged-in member sees an "I'm playing" button on a `draft`-status public tournament page (or a members-only pre-registration list), which creates a `tournament_participants` row (or a pending-player row if they're not yet in `players`) tied to their account, subject to a cutoff the tournament director sets (e.g., registration closes N hours before round 1 or when the director flips status to `active`). This would remove a recurring manual step for the ACG staff before every event without requiring any new roles or major schema changes — it mostly reuses `add_participant()` and the existing pending-player reconciliation flow, gated by a new `registration_open` boolean/timestamp on `tournaments`.