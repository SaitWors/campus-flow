#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ ! -f .env ]]; then
    umask 077
    if ! command -v openssl >/dev/null; then
        echo 'Install openssl, then run this script again.' >&2
        exit 1
    fi
    {
        echo 'APP_ORIGIN=http://localhost:8080'
        echo 'HTTP_PORT=8080'
        echo 'BIND_ADDRESS=127.0.0.1'
        echo 'COOKIE_SECURE=false'
        for key in INTERNAL_TOKEN SETUP_KEY AUTH_DB_PASSWORD SCHEDULE_DB_PASSWORD QUEUE_DB_PASSWORD NOTIFICATIONS_DB_PASSWORD; do
            secret=$(openssl rand -hex 32)
            printf '%s=%s\n' "$key" "$secret"
        done
    } > .env
    echo 'Created .env with unique keys. Keep it with your backups.'
fi
# Upgrade an existing PR1 .env without rotating any existing key.
if ! grep -q '^NOTIFICATIONS_DB_PASSWORD=' .env; then
    umask 077
    notification_secret=$(openssl rand -hex 32)
    printf '\nNOTIFICATIONS_DB_PASSWORD=%s\n' "$notification_secret" >> .env
fi
docker compose version
docker compose up --build -d --wait --wait-timeout 180
echo 'Open APP_ORIGIN from .env (default http://localhost:8080).'
echo 'First account: copy SETUP_KEY from .env into the first-run form.'
