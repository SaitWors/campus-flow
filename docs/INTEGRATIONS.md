# Integrations and API

The gateway exposes same-origin endpoints. OpenAPI schemas are available at:

- `/api/auth/openapi.json`
- `/api/schedule/openapi.json`
- `/api/queues/openapi.json`

The public gateway denies `/internal/`. Never put `INTERNAL_TOKEN` in browser JavaScript or distribute it to students. Internal routes appear in each service's raw schema, but remain inaccessible through the public gateway.

## Authentication for an approved account

`POST /api/auth/login` accepts `email` and `password`, sets `cf_session` and returns a CSRF token. Keep the cookie in a cookie jar; pass `X-CSRF-Token` on mutations. Sessions expire after seven days. Blocking, password reset and password changes may revoke them sooner.

Use a minimally privileged approved account for a read-only external integration. A manager account is required for schedule/queue management; there are no dedicated service accounts, OAuth scopes or personal access tokens in this version. Do not treat a browser session as a permanent bot credential.

## Main endpoints

| Method / path | Purpose |
| --- | --- |
| GET `/api/auth/me` | Current approved account and CSRF token |
| GET `/api/schedule/settings` | Group, timezone, semester and reference parity |
| GET `/api/schedule/occurrences?start=2026-09-01&end=2026-09-30` | Classes in a date range, max 93-day difference |
| GET `/api/schedule/occurrences/{id}` | One class with current revision and timezone-aware instants |
| GET `/api/schedule/rules` | Manager-only semester templates |
| POST `/api/schedule/rules` | Create a recurring template |
| PATCH `/api/schedule/occurrences/{id}` | Replace editable fields of one occurrence; include current revision |
| GET `/api/queues?mine=true` | Queues in which this user has an active place |
| GET `/api/queues?occurrence_id={id}` | Queue associated with a particular class |
| POST `/api/queues` | Create a class queue, manager only |
| GET `/api/queues/{id}` | Queue, active places, history and caller's position |
| POST `/api/queues/{id}/actions` | Join, leave, open, pause, next, done, skip, remove, close or configure |
| GET `/api/schedule/events?after=0` | Up to 200 schedule events after a cursor |

For a join:

```http
POST /api/queues/QUEUE_ID/actions
Content-Type: application/json
X-CSRF-Token: TOKEN_FROM_LOGIN
Idempotency-Key: A_NEW_UUID_FOR_THIS_ACTION
Cookie: cf_session=SESSION_COOKIE

{"action":"join","task":"Лабораторная № 2"}
```

Reuse the same idempotency key and same body when retrying an uncertain response. Generate a new key for a new intentional action. Manager commands additionally pass the latest queue `revision`; for example `{"action":"next","revision":8}`. Clients must handle 401/403 access failures, 409 business conflicts, 422 field validation and 503 upstream unavailability.

## Events

Read `/api/schedule/events?after=LAST_SEQ`. Process the returned records in sequence, then persist the last successfully handled `seq`. Start with 0 for a full history. Records include `id`, `type`, `data.occurrence_id`, `data.revision` and UTC `at`. Types currently include occurrence.created, occurrence.updated and occurrence.cancelled.

The integration is responsible for de-duplication, retry policy and its cursor. This is HTTP polling, not webhooks or WebSockets. A Telegram notification service could poll these events and fetch the current class, but Telegram authentication, recipient consent/mapping and delivery are not implemented. Queue events are currently recorded in its audit log, without a public sequential event stream.

## iCalendar

`GET /api/schedule/calendar.ics?start=YYYY-MM-DD&end=YYYY-MM-DD&lang=ru&subgroup=1` exports an authenticated snapshot. `subgroup=0` includes both subgroups. `lang=en` uses an English subject title when one is provided. UIDs remain stable across moves; cancelled classes are exported with CANCELLED status. Lines are folded to 75 UTF-8 octets.

Import the `.ics` into Google Calendar, Apple Calendar or Outlook. This is not a persistent public subscription link: calendar applications cannot use the site's login cookie in a subscription. Re-export to obtain changes; external calendar importers vary in how they handle updates and removals. Sharing public subscription tokens would require a separate, deliberate feature.


## Title translation

Responses add title_en_auto and translation_pending; do not send these computed fields in mutation bodies. Empty title_en enables automatic translation. Manager-only POST /api/schedule/title-preview accepts {"title":"Базы данных"} with cookie/CSRF protection. It accepts no provider URLs or keys. English ICS uses manual, automatic, then original titles.
