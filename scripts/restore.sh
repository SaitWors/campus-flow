#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
backup_dir="${1:?Usage: bash scripts/restore.sh backups/YYYYMMDDTHHMMSSZ}"
[[ ! -L "$backup_dir" ]] || { echo 'Backup directory may not be a symlink.' >&2; exit 1; }
backup_abs="$(cd "$backup_dir" && pwd)"
legacy_args=()
if [[ ! -f "$backup_abs/manifest.json" ]]; then
    schemas=()
    for service in auth schedule notifications; do
        # Inspect the historical dump without connecting to or replacing a DB.
        version="$(docker run --rm --network none --volume "$backup_abs:/backup:ro" postgres:17-alpine pg_restore -a -t schema_migrations "/backup/$service.dump" | docker compose run --rm --no-deps -T schedule python -m scripts.materials_archive schema-version)"
        schemas+=("$service:$version")
    done
    schema_csv="$(IFS=,; echo "${schemas[*]}")"
    legacy_args=(--schemas "$schema_csv")
fi
verify=(docker compose run --rm --no-deps -T --user "$(id -u):$(id -g)" --volume "$backup_abs:/backup:ro" schedule python -m scripts.materials_archive)
# Every checksum, archive path/type and per-file hash is checked before even
# the safety backup stops writers. The only data in this variable is metadata.
header="$("${verify[@]}" backup-header /backup "${legacy_args[@]}" </dev/null)"
for service in auth schedule notifications; do
    docker run --rm --network none --volume "$backup_abs:/backup:ro" postgres:17-alpine pg_restore --list "/backup/$service.dump" > /dev/null
done
echo 'This replaces ALL current accounts, schedule, notifications and material files with the selected backup.'
read -r -p 'Type RESTORE to continue: ' restore_confirmation
[[ "$restore_confirmation" == RESTORE ]] || exit 1
bash scripts/backup.sh
docker compose stop web notifications schedule auth
echo 'Application stays stopped if restoration fails. Restore all databases and files before restarting.'
restore_stage=''
cleanup() {
    if [[ -n "$restore_stage" ]]; then
        docker compose run --rm --no-deps -T schedule python -m scripts.materials_archive discard /var/lib/campus/materials "$restore_stage"
    fi
}
trap cleanup EXIT
restore_stage="$( { printf '%s\n' "$header"; if [[ -f "$backup_abs/materials.tar" ]]; then cat "$backup_abs/materials.tar"; fi; } | docker compose run --rm --no-deps -T schedule python -m scripts.materials_archive stage-stream /var/lib/campus/materials )"
for service in auth schedule notifications; do
    docker compose exec -T "$service-db" psql -X -v ON_ERROR_STOP=1 -U "$service" -d "$service" -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public AUTHORIZATION $service;"
    docker compose exec -T "$service-db" pg_restore -U "$service" -d "$service" --no-owner --exit-on-error < "$backup_abs/$service.dump"
done
schema="$(docker compose exec -T schedule-db psql -X -At -U schedule -d schedule -c 'SELECT MAX(version) FROM schema_migrations;')"
if ((schema >= 5)); then
    docker compose exec -T schedule-db psql -X -At -U schedule -d schedule -c "SELECT COALESCE(json_agg(json_build_object('id', id, 'bytes', size_bytes, 'sha256', sha256)), '[]') FROM materials;" | "${verify[@]}" verify-metadata /backup "${legacy_args[@]}"
fi
docker compose run --rm --no-deps -T schedule python -m scripts.materials_archive publish /var/lib/campus/materials "$restore_stage"
restore_stage=''
docker compose up -d --wait --wait-timeout 180
echo 'Restoration completed.'
