# Architecture

Campus Flow has three independently owned APIs. Services authenticate internal requests with `INTERNAL_TOKEN` and never query another service's database. The gateway exposes only same-origin `/api/auth`, `/api/schedule` and `/api/notifications`; `/internal` is private. Production runs web, three APIs and one PostgreSQL instance with three isolated non-superuser roles/databases. Development Compose runs three separate PostgreSQL instances and optionally the offline translation model.

| Owner | Data | Invariants |
| --- | --- | --- |
| auth, schema 3 | Members, invitations, roles, sessions, TOTP/recovery, audit | Active account, password/MFA checks, CSRF, single-use recovery, session revocation |
| schedule, schema 4 | Semester, templates, occurrences, subject details, time presets, assignments, personal progress, events, translations | Timezone-aware instants, conflicts, revisions, manager edits, subgroup access, private progress |
| notifications, schema 3 | Inbox, preferences, announcements, questions, push subscriptions/VAPID, Telegram links/bindings/delivery, cursors | Recipient ownership, live audience checks, quiet hours, privacy, durable retries |

## Consistency

Schedule writes and their event records commit in one transaction. Readers use durable sequence cursors. Event metadata contains a safe before/after projection of lesson title, date, time, room, status and subgroup; private notes and meeting links are excluded. Assignment events include only delivery metadata. Member event filtering happens before pagination and also checks the current assignment audience.

Mutations require the current revision and return 409 on concurrent changes. Personal progress is keyed by assignment and authenticated member, and protected by a unique key for the first-write race. Managers cannot retrieve another member's progress. Internal reminder metadata is available only to notifications; it excludes already-completed recipients.

The notification worker groups committed timetable changes, persists inbox and delivery jobs with cursor advancement, and rechecks account/audience/delivery state before sending. Web Push is tied to a verified browser session. Telegram uses an explicit one-time private-chat binding and checks active membership independently of browser session lifetime. Delivery leases and bounded retries survive restarts; an external transport timeout can still produce a duplicate after uncertain delivery.

## Runtime and frontend

Internal HTTP calls reuse a bounded keep-alive connection pool. Database pools in production allow two retained plus two overflow connections per API. Inbox and academic audiences are filtered in SQL. The unused fourth API, its event worker, database initialization, proxy and polling are removed.

The web shell loads larger sections on demand. Today shows an active/upcoming class and personal deadlines; schedule preserves day/week/month filters per account on the device. Mobile navigation prioritizes the daily sections and groups secondary sections under More. Background refresh stops when the page is hidden.

Public guest APIs, calendar subscription and explicitly saved offline data expose only public schedule fields. Private HTML/API responses are not persisted by the Service Worker. Public subject keys and assignment details are not added to guest responses.

## Operations

Images are tagged by full commit SHA, with exact supported schema metadata. Deployment checks metadata before maintenance and backs up existing databases. An older incompatible image is rejected before mutation. Dumps are compared by table/row digest before schema upgrades; migrations are idempotent.

New backups contain three active databases. Existing four-database backups are verified in full, while only active databases are restored. Retired volumes are preserved. The external backup command checks a private archive, transfers it using SSH with pinned host verification, checks remote SHA-256, then atomically publishes it. A systemd timer template schedules daily copies; operators supply the destination and retention policy.

See [study hub and update](STUDY_HUB_RU.md), [notifications](NOTIFICATIONS_RU.md) and [production operations](DEPLOY_TIMEWEB40_RU.md).
