#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
umask 077
mkdir -p backups
backup_dir="backups/$(date -u +%Y%m%dT%H%M%SZ)-${RANDOM}-${RANDOM}"
mkdir "$backup_dir"
backup_abs="$(cd "$backup_dir" && pwd)"
cp .env "$backup_dir/config.env"
docker compose config --format json > "$backup_dir/effective-config.json"
active=()
while IFS= read -r service; do
    case "$service" in auth|schedule|notifications|web) active+=("$service");; esac
done < <(docker compose ps --services --filter status=running)
resume() {
    if ((${#active[@]})); then docker compose start "${active[@]}"; fi
}
trap resume EXIT
docker compose stop web notifications schedule auth
schemas=()
for service in auth schedule notifications; do
    version="$(docker compose exec -T "$service-db" psql -X -At -U "$service" -d "$service" -c 'SELECT MAX(version) FROM schema_migrations;')"
    [[ "$version" =~ ^[0-9]+$ ]] || { echo "Invalid schema version: $service" >&2; exit 1; }
    schemas+=("$service:$version")
    if [[ "$service" == schedule ]]; then schedule_schema="$version"; fi
    docker compose exec -T "$service-db" pg_dump -U "$service" -d "$service" -Fc > "$backup_dir/$service.dump"
    test -s "$backup_dir/$service.dump"
done
# stdout goes directly to disk; /tmp and the shell never hold the archive.
docker compose run --rm --no-deps -T schedule python -m scripts.materials_archive create /var/lib/campus/materials > "$backup_dir/materials.tar"
material_metadata() {
    if ((schedule_schema >= 5)); then
        docker compose exec -T schedule-db psql -X -At -U schedule -d schedule -c "SELECT COALESCE(json_agg(json_build_object('id', id, 'bytes', size_bytes, 'sha256', sha256)), '[]') FROM materials;"
    else printf '[]\n'; fi
}
schema_csv="$(IFS=,; echo "${schemas[*]}")"
# This helper only reads the host backup. Run it as the host owner so the
# backup directory can stay private instead of granting UID 10001 access.
material_metadata | docker compose run --rm --no-deps -T --user "$(id -u):$(id -g)" --volume "$backup_abs:/backup" schedule python -m scripts.materials_archive finish-backup /backup --schemas "$schema_csv" --metadata-stdin
echo "Backup completed: $backup_dir"
echo 'Keep the entire folder private; it contains accounts, material files and server keys.'
