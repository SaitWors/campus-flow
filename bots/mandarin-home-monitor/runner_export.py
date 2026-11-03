"""Send a WireGuard SSH sample to the registered Campus Flow container."""
import json
import os
import re
import subprocess
import time
from pathlib import Path

from bot import fetch_metrics


CONFIG_DIRECTORY = Path("/etc/mandarin-home-bot")
STANDALONE_STATE = Path("/var/lib/mandarin-home-bot/state.json")
CONTAINER = re.compile(r"cfbot-([a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12})\Z")
IMAGE = re.compile(r"ghcr\.io/saitwors/campus-flow-home-monitor@sha256:[a-f0-9]{64}\Z")


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def docker(*arguments, payload=None):
    return subprocess.run(
        ["/usr/bin/docker", *arguments], input=payload,
        capture_output=True, text=True, timeout=12,
    )


def main():
    target = read_json(CONFIG_DIRECTORY / "runner.json")
    match = CONTAINER.fullmatch(target["container"])
    if match is None or IMAGE.fullmatch(target["image"]) is None:
        raise SystemExit("Invalid Campus Flow container or image")

    standalone = subprocess.run(
        ["/usr/bin/systemctl", "show", "mandarin-home-bot.service", "-p", "ActiveState", "--value"],
        capture_output=True, text=True, timeout=5, check=True,
    )
    if standalone.stdout.strip() not in ("inactive", "failed"):
        return

    found = docker(
        "inspect", "--format",
        '{{.Id}} {{.State.Running}} {{index .Config.Labels "campus-flow.bot-managed"}} '
        '{{index .Config.Labels "campus-flow.bot-id"}} {{.Config.Image}}',
        target["container"],
    )
    if found.returncode:
        return
    container_id, running, managed, bot_id, image = found.stdout.split()
    if managed != "true" or bot_id != match[1] or image != target["image"]:
        raise SystemExit("Container does not match the registered Campus Flow bot")
    if running != "true":
        return

    config = read_json(CONFIG_DIRECTORY / "telegram.json")
    offset = config["offset"]
    if STANDALONE_STATE.is_file():
        offset = read_json(STANDALONE_STATE).get("offset", offset)
    snapshot = {
        "owner": config["owner"],
        "offset": offset,
        "timestamp": time.time(),
        "metrics": fetch_metrics(Path(os.environ["CREDENTIALS_DIRECTORY"])),
    }
    written = docker(
        "exec", "-i", "--user", "10001:10001", container_id,
        "python", "/app/bot.py", "ingest",
        payload=json.dumps(snapshot, allow_nan=False),
    )
    if written.returncode:
        raise SystemExit("The Campus Flow bot did not accept the metrics snapshot")


if __name__ == "__main__":
    main()
