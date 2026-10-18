#!/usr/bin/env bash
# Isolated CI only: real nginx, real bot JSON API and the browser workflow.
set -Eeuo pipefail
[[ ${CAMPUS_DISPOSABLE:-} == 1 ]] || { echo 'Requires CAMPUS_DISPOSABLE=1 on a disposable Docker host' >&2; exit 1; }
HB_IMAGE=${1:?Pass the built habit bot image}
HB_ROOT=$(git rev-parse --show-toplevel)
HB_BOT=cfbot-10000000-0000-0000-0000-000000000001
HB_WEB="habits-gateway-test-${RANDOM}"
HB_NET=campus-flow-habits
if docker inspect "$HB_BOT" >/dev/null 2>&1 || docker network inspect "$HB_NET" >/dev/null 2>&1; then
  echo 'Refusing to reuse existing fixture container or miniapp network' >&2
  exit 1
fi
docker network create --label com.campus-flow.miniapp-network=habits-v1 "$HB_NET" >/dev/null
cleanup() {
  HB_EXIT=$?
  if [[ "$HB_EXIT" != 0 ]]; then
    docker logs "$HB_BOT" 2>&1 || true
    docker logs "$HB_WEB" 2>&1 || true
  fi
  docker rm -f "$HB_WEB" "$HB_BOT" >/dev/null 2>&1 || true
  docker network rm "$HB_NET" >/dev/null 2>&1 || true
  return "$HB_EXIT"
}
trap cleanup EXIT
docker run -d --name "$HB_BOT" --network "$HB_NET" --read-only --user 10001:10001 \
  --tmpfs /tmp:rw,nosuid,nodev,size=32m --memory 192m --cpus 0.35 \
  --cap-drop ALL --security-opt no-new-privileges:true \
  --mount "type=bind,source=$HB_ROOT/bots/mandarin-habits/tests/serve_miniapp.py,target=/fixture.py,readonly" \
  --entrypoint python "$HB_IMAGE" -c 'import runpy,sys;sys.argv=["fixture.py","--api-only"];runpy.run_path("/fixture.py",run_name="__main__")' >/dev/null
docker run -d --name "$HB_WEB" --network "$HB_NET" -p 127.0.0.1:8765:8080 \
  --mount "type=bind,source=$HB_ROOT/infra/nginx.conf,target=/etc/nginx/conf.d/default.conf,readonly" \
  --mount "type=bind,source=$HB_ROOT/infra/habits-headers.conf,target=/etc/nginx/habits-headers.conf,readonly" \
  --mount "type=bind,source=$HB_ROOT/bots/mandarin-habits/web,target=/usr/share/nginx/html/habits-app,readonly" \
  nginx:1.28-alpine >/dev/null
for HB_ATTEMPT in {1..40}; do
  if [[ $(curl --silent -o /dev/null -w '%{http_code}' http://127.0.0.1:8765/habits/10000000-0000-0000-0000-000000000001/api/state || true) == 401 ]]; then break; fi
  sleep 1
done
docker exec "$HB_WEB" nginx -t
HABIT_GATEWAY=1 node "$HB_ROOT/scripts/check_habit_miniapp_ui.cjs"
