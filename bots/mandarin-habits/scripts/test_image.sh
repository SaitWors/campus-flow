#!/usr/bin/env bash
set -euo pipefail

HB_IMAGE=${1:?Pass a locally built image}
HB_VOLUME="habitbot-ci-${RANDOM}-${RANDOM}"
HB_TMP=$(mktemp -d)
docker volume create "$HB_VOLUME" >/dev/null
trap 'docker volume rm "$HB_VOLUME" >/dev/null; rm -rf "$HB_TMP"' EXIT

# Same rootfs/user/tmp restrictions as Campus Flow; no token or network needed.
docker run --rm --network none --read-only --user 10001:10001 \
  --tmpfs /tmp:rw,nosuid,nodev,size=32m \
  --entrypoint python "$HB_IMAGE" -c \
  'from habitbot.service import Service; s=Service("/tmp/test.sqlite3",123456); s.register(123456); assert len(s.goals())==2; s.close()'

# The image must initialise /data for UID 10001, then keep SQLite across containers.
docker run --rm --network none --read-only --user 10001:10001 \
  --tmpfs /tmp:rw,nosuid,nodev,size=32m \
  --mount "type=volume,source=$HB_VOLUME,target=/data" \
  --entrypoint python "$HB_IMAGE" -c \
  'from habitbot.service import Service; s=Service("/data/state.sqlite3",123456); s.register(123456); s.complete(s.goals()[0]["id"]); assert s.profile()["coins"]>0; s.close()'

docker run --rm --network none --read-only --user 10001:10001 \
  --tmpfs /tmp:rw,nosuid,nodev,size=32m \
  --mount "type=volume,source=$HB_VOLUME,target=/data" \
  --entrypoint python "$HB_IMAGE" -c \
  'from habitbot.service import Service; s=Service("/data/state.sqlite3",123456); assert s.profile()["coins"]>0; s.close()'

# Root on a VPS can retain a 0600 backup. Only its bytes enter the non-root
# container through stdin, avoiding bind-mount ownership and WAL-sidecar issues.
umask 077
docker run --rm --network none --read-only --user 10001:10001 \
  --mount "type=volume,source=$HB_VOLUME,target=/data" \
  --env BOT_DATA_DIR=/data --entrypoint python "$HB_IMAGE" \
  -m habitbot.backup > "$HB_TMP/restore.sqlite3"

docker run --rm -i --network none --read-only --user 10001:10001 \
  --tmpfs /tmp:rw,nosuid,nodev,size=32m \
  --mount "type=volume,source=$HB_VOLUME,target=/data" \
  --env BOT_DATA_DIR=/data --env OWNER_ID=123456 \
  --entrypoint python "$HB_IMAGE" -m habitbot.restore - < "$HB_TMP/restore.sqlite3"

printf '%s\n' 'Image and persistent storage checks passed.'
