#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
umask 077
backup_dir="backups/$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$backup_dir"
cp .env "$backup_dir/config.env"
docker compose stop web queue schedule auth
trap 'docker compose start auth schedule queue web' EXIT
for service in auth schedule queue; do
    docker compose exec -T "$service-db" pg_dump -U "$service" -d "$service" -Fc > "$backup_dir/$service.dump"
    test -s "$backup_dir/$service.dump"
done
touch "$backup_dir/COMPLETE"
echo "Backup completed: $backup_dir"
echo 'Keep the entire folder private; it contains accounts and server keys.'
