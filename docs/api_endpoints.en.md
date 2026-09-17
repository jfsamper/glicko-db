# Routes and integration notes

See the [User Interface Help](user_interface.en.md) for task-oriented instructions and the [English README](../README.en.md) for installation and configuration.

## Table of contents

- [API status](#api-status)
- [Public GET routes](#public-get-routes)
- [Authentication and member routes](#authentication-and-member-routes)
- [Administrative route groups](#administrative-route-groups)
- [Integration guidance](#integration-guidance)

## API status

The current application is a server-rendered Flask web application. It does **not** currently publish a stable public REST API under `/api`. The paths `/api/backup`, `/api/player`, `/api/tournament`, and similar paths from the previous draft are not implemented endpoints and should not be used by integrations.

Administrative actions are HTML form submissions. They require an authenticated session, the appropriate role, and a valid CSRF token. Some asynchronous admin actions return a small JSON redirect response only when the request includes `X-Requested-With: XMLHttpRequest`; this is an internal browser behavior, not a versioned API contract.

## Public GET routes

These pages can be opened without an administrator account:

| Path | Purpose |
| --- | --- |
| `/` | Home page, rankings summary, statistics, and published news |
| `/rankings` | Paginated public rankings |
| `/players` | Player directory and filters |
| `/player/view?id=<player_id>` | Player profile and match history |
| `/matches` | Match list and filters |
| `/tournaments` | Public tournament list |
| `/tournaments/<tournament_id>` | Public tournament details, pairings, and standings |
| `/reports` | Period and player reports |
| `/reports/export.csv` | CSV version of the selected report filters |
| `/reports/export.pdf` | PDF version of the selected report filters |
| `/category` | Glicko rating and category information |
| `/sgf-library` | Public SGF library |
| `/sgf-library/<filename>` | View metadata for an SGF record |
| `/sgf/<filename>` | Download an SGF record |
| `/matches/<match_id>/record` | View the SGF record linked to a match |
| `/matches/<match_id>/sgf` | Download the SGF record linked to a match |
| `/news/<article_id>` | Published news article |

Most pages accept an optional `lang=es`, `lang=en`, or `lang=pt` query parameter. Report pages also accept `start_date`, `end_date`, and `player_id` filters; see the user guide for examples.

## Authentication and member routes

| Path | Method | Access |
| --- | --- | --- |
| `/admin/login` | GET, POST | Sign in |
| `/admin/register` | GET, POST | Create a member account |
| `/admin/forgot-password` | GET, POST | Request a password-reset link |
| `/admin/reset-password/<token>` | GET, POST | Complete a password reset |
| `/admin/report-results` | GET, POST | Members submit a result involving their linked player |
| `/admin/profile` | GET, POST | Authenticated users manage their profile |
| `/admin/logout` | GET | End the current session |

Member submissions remain pending until reviewed. They do not affect public matches, ratings, or reports before approval.

## Administrative route groups

All routes below require a signed-in account and role permissions. The forms and available actions are documented in [User Interface Help](user_interface.en.md).

- `/admin`: administration dashboard
- `/admin/import`: Excel, CSV, and OpenGotha imports
- `/admin/backups`: create, restore, and delete managed backups
- `/admin/players`, `/admin/matches`: player and match management
- `/admin/ratings`, `/admin/categories`: rating and category configuration
- `/admin/tournaments`: tournament creation and management
- `/admin/tournaments/<tournament_id>/...`: participants, pairings, results, round processing, and export
- `/admin/sgf/...`: SGF linking and deletion actions
- `/admin/news`: news management
- `/admin/users`, `/admin/audit`, `/admin/settings`: account, audit, and application settings

POST routes change application state and should be called through the rendered forms. Backup, restore, import, deletion, and tournament actions are deliberately not exposed as unauthenticated GET operations.

## Integration guidance

For an external integration, use the documented CSV/PDF report exports or the database only through an approved operational process. Do not scrape admin forms or depend on undocumented JSON responses. A future REST API should define authentication, request schemas, response schemas, error codes, pagination, and versioning before clients are built.
