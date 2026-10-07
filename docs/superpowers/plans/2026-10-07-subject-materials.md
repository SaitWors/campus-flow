# Subject Materials Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans for owned tasks and superpowers:dispatching-parallel-agents for independent UI/operations tasks. Do not edit another worker's files.

**Goal:** Store group lecture/notes files in their subject pages; managers upload and members read.

**Architecture:** Schedule owns metadata and a private persistent file volume. Raw byte upload streams to disk; authenticated responses expose files. Operations back up both DB and files.

**Tech Stack:** FastAPI, SQLAlchemy, Python stdlib/AnyIO, React/TypeScript, Docker Compose.

**Spec:** docs/superpowers/specs/2026-10-07-subject-materials.md

## Global Constraints
- Follow the API, 50MiB file and 5GiB default quota, supported types and user-selected manager-only writes in the spec.
- RU/EN and existing themes/roles/auth/CSRF remain functional; no public files/offline cache.
- Schedule5/auth3/notifications3; preserve secrets, old data and Docker volumes. No live deploy, real user uploads or merge.
- Baseline and feature tests before code; report failures honestly.

## Review Focus
- Uploaded image filename cannot allow active HTML/SVG or filesystem traversal.
- Simultaneous uploads cannot exceed quota; disconnect/DB/disk failures cannot expose partial material.
- Revoked/guest accounts cannot download by direct URL or through an old browser cache.
- File volume and database point must stay consistent across backup/restore and failed migration.
- A large file must stream under production memory limits and through both proxy limits.

## Task 1: backend (root)
Files: services/schedule/{materials.py,models.py,main.py}, services/common/core.py, tests/test_materials*.py, tests/test_study_migrations.py, scripts/verify_materials.py, tests/test_http.py.
- [x] Write failing real HTTP/storage tests for manager writes, member reads, limits, CSRF, private images, search, revisions, persistence and error cleanup.
- [x] Run focused tests and confirm missing endpoint/storage behavior fails.
- [x] Add migrate_materials and focused storage/routes module. Register with DB; adapt shared body limit only for exact upload route.
- [x] Run focused/full Python tests and actual HTTP integration; correct schema expectations.

## Task 2: UI (materials_ui worker)
Files: apps/web/**, scripts/check_materials_ui.cjs.
- [x] Write failing meaningful tests for upload client/helpers and filtering/formatting as appropriate.
- [x] Add materials component to subject pages: file selection/multiple upload, categories/search/page, preview, download, manager edits/delete.
- [x] Consume exact API above; use authenticated binary upload helper and explicit error/cancel behavior; no automatic file caching.
- [x] Run frontend tests/build; add actual browser material workflow script.

## Task 3: operations (materials_ops worker)
Files: compose*.yaml, infra/**, scripts/{production.py,prod_smoke.py,backup*,restore*,dev.py,verify_production.py,external_backup.py}, tests/{test_production_operations.py,test_external_backup.py,test_resource_limits.py,test_version_observability.py}, .github/workflows/ci.yml, docs deployment/operations/materials usage; .gitignore.
- [x] Write failing material archive/restore tests (tamper, traversal, missing files in schema5, old backup, roundtrip).
- [x] Add persistent volume/nonroot ownership, 50MiB exact upload proxy limit/streaming and local storage paths.
- [x] Include private materials archive in all backup/restore/migration/external-copy paths; verify before mutation. Keep old installs safe.
- [x] Update schema5 metadata/constants/operations docs and CI material API/browser checks; run focused checks.

## Task 4: integration/review/PR (root)
- [x] Review independent work and run full Python/frontend/build/browser verification.
- [x] Obtain independent review of backend/storage and backup integration; fix findings.
- [ ] Commit validated tree and publish a new PR against current main, then inspect CI.

## Verification at publication

- Full Python suite: 168 passed. Frontend: 36 passed; production build succeeded.
- Real HTTP includes exact 50MiB streaming and maximum Unicode metadata; fragmented request-line regression passed.
- Material browser workflow passed after the committed-write/500-response regression fix. Existing study/PR3/community/notifications UI workflows passed; local notifications run explicitly excluded push lifecycle.
- Independent review reproduced cancellation, SIGKILL upload/stage/publish recovery, legacy COPY parsing and pre-mutation snapshot rejection; no remaining Critical/Important findings.
- Local Docker/Nginx and PowerShell runtimes were unavailable. Docker/gateway/backup end-to-end checks run in GitHub CI; PowerShell runtime remains unverified.
