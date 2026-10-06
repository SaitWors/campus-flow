# Integrations and API

OpenAPI: `/api/auth/openapi.json`, `/api/schedule/openapi.json`, `/api/notifications/openapi.json`. The public gateway denies `/internal/*`. Never distribute `INTERNAL_TOKEN` to browsers or members.

## Authentication

`POST /api/auth/login` accepts email/password and sets a seven-day `cf_session`, or returns a short-lived MFA challenge. Approved users call `/api/auth/me` to obtain their identity and CSRF token. Mutations require `X-CSRF-Token`. Sessions can be revoked earlier by security changes. External read-only clients should use minimally privileged accounts; no service-account/PAT scheme is provided.

## Academic API

| Method and path | Purpose |
| --- | --- |
| GET `/api/schedule/settings` | Semester and timezone |
| GET `/api/schedule/occurrences?start=YYYY-MM-DD&end=YYYY-MM-DD` | Classes; max 93-day difference |
| GET `/api/schedule/occurrences/{id}` | Current class and revision |
| GET/POST `/api/schedule/rules` | Manager-only recurring templates |
| PATCH/DELETE `/api/schedule/occurrences/{id}` | Manager edits/removes one class; revision required |
| GET `/api/schedule/subjects` | Member subject catalogue/details |
| GET/PATCH `/api/schedule/subjects/{key}` | Read details / manager updates teacher, requirements and HTTPS links |
| GET `/api/schedule/assignments?subgroup=0&subject_key=...` | Active assignments with only the caller's progress |
| POST `/api/schedule/assignments` | Manager creates subject/title/description/due_at/subgroup/material_url |
| PUT/DELETE `/api/schedule/assignments/{id}` | Manager updates / archives; revision required |
| PUT `/api/schedule/assignments/{id}/progress` | Own status and expected progress revision; initial revision 0 |
| GET `/api/schedule/events?after=SEQ` | Up to 200 events permitted for the current member |

`due_at` is a timezone-aware ISO instant or null. Progress statuses: `not_started`, `in_progress`, `ready`, `done`. Students see common assignments plus their current subgroup; selecting another subgroup is forbidden. Progress is personal, including for managers. Clients handle 401/403, 404 for inaccessible items, 409 for stale changes, 422 validation and 503 upstream failures.

## Events and notifications

Persist the last successfully processed `seq` and deduplicate/retry deliberately. Lesson events include safe before/after projections; assignment events contain id, revision, subject/title, due instant and subgroup. This is HTTP polling, not a webhook. No private question text is included in delivery messages.

The notifications API owns inbox/read state, category/language/privacy/quiet-hour preferences, subscriptions, announcements and private questions. Telegram endpoints: `GET /telegram`, `POST /telegram/link`, `DELETE /telegram`, `POST /telegram/test` under `/api/notifications`. Linking is explicit, CSRF protected, one-time and expires after ten minutes. See [channel setup](NOTIFICATIONS_RU.md).

## Calendar and translation

Authenticated `/api/schedule/calendar.ics?start=...&end=...&lang=ru&subgroup=1` exports a snapshot. Public `/api/schedule/guest/calendar.ics?lang=ru&subgroup=1` is the persistent limited subscription URL. Stable UIDs and cancellation tombstones let calendar clients reconcile changes; refresh cadence depends on the client.

Computed `title_en_auto` and `translation_pending` are response-only. Empty manual `title_en` permits optional offline translation. Manager `POST /api/schedule/title-preview` accepts only a title, with session/CSRF checks. ICS uses manual English, automatic English, then the original title. External timetable import is not implemented.
