#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
backup_dir="${1:?Usage: bash scripts/restore.sh backups/YYYYMMDDTHHMMSSZ}"
for file in COMPLETE auth.dump schedule.dump queue.dump notifications.dump; do
    test -f "$backup_dir/$file" || { echo "Missing backup file: $file" >&2; exit 1; }
done
echo 'This replaces ALL current accounts, schedule, queues and notifications with the selected backup.'
read -r -p 'Type RESTORE to continue: ' restore_confirmation
[[ "$restore_confirmation" == RESTORE ]] || exit 1
docker compose stop web notifications queue schedule auth
echo 'Application stays stopped if restoration fails. Do not start it until all four databases are restored.'
for service in auth schedule queue notifications; do
    docker compose exec -T "$service-db" pg_restore -U "$service" -d "$service" --clean --if-exists --no-owner --exit-on-error < "$backup_dir/$service.dump"
done
docker compose up -d --wait --wait-timeout 180
echo 'Restoration completed.'
