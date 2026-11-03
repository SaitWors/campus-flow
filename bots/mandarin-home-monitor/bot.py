import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


SAMPLE_INTERVAL = 60


class TelegramError(Exception):
    pass


def telegram(token, method, **parameters):
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/{method}",
        data=json.dumps(parameters).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=25) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as error:
        raise TelegramError(f"Telegram {method}: HTTP {error.code}") from None
    except (OSError, ValueError):
        raise TelegramError(f"Telegram {method}: connection or response error") from None
    if not payload.get("ok"):
        raise TelegramError(f"Telegram {method}: request rejected")
    return payload["result"]


def private_sender(message):
    chat = message.get("chat", {})
    chat_id = chat.get("id")
    if chat.get("type") == "private" and type(chat_id) is int:
        if chat_id == message.get("from", {}).get("id"):
            return chat_id
    return None


def owner_command(message, owner):
    if private_sender(message) != owner:
        return None
    words = message.get("text", "").split()
    return words[0] if words and words[0] in ("/start", "/status") else None


def status_change(has_sample, failures, alerted):
    if has_sample:
        return "recovered" if alerted else None
    return "offline" if failures >= 3 and not alerted else None


def write_json(path, payload):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload), encoding="utf-8")
    temporary.replace(path)


def read_snapshot(data):
    try:
        snapshot = json.loads((data / "host.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if isinstance(snapshot, dict) and type(snapshot.get("owner")) is int and snapshot["owner"] > 0:
        return snapshot
    return None


def fetch_metrics(data, owner, now):
    snapshot = read_snapshot(data)
    if snapshot is None or snapshot["owner"] != owner:
        return None
    timestamp = snapshot.get("timestamp")
    metrics = snapshot.get("metrics")
    if isinstance(timestamp, (int, float)) and 0 <= now - timestamp <= 120 and isinstance(metrics, dict):
        return metrics
    return None


def ingest():
    snapshot = json.loads(sys.stdin.buffer.read(8193))
    if not isinstance(snapshot, dict) or type(snapshot.get("owner")) is not int or snapshot["owner"] <= 0:
        raise SystemExit("Invalid owner")
    write_json(Path(os.environ.get("BOT_DATA_DIR", "/data")) / "host.json", snapshot)


def format_status(sample, sampled_at, failures):
    if sample is None:
        return "Данные с домашнего ноутбука пока не поступили."
    sampled = sampled_at.strftime("%d.%m.%Y %H:%M:%S МСК")
    hours, remainder = divmod(sample["uptime_seconds"], 3600)
    lines = [
        f"Домашний сервер · {sampled}",
        f"CPU: {sample['cpu_pct']:.1f}%",
        f"Память: {sample['ram_used_gib']:.1f} / {sample['ram_total_gib']:.1f} ГиБ",
        f"Диск: {sample['disk_free_gib']:.1f} ГиБ свободно из {sample['disk_total_gib']:.1f}",
        f"Аптайм: {hours} ч {remainder // 60} мин",
    ]
    if sample.get("temperature_c") is not None:
        lines.append(f"Температура CPU: {sample['temperature_c']:.1f} °C")
    if sample.get("battery_pct") is not None:
        power = "от сети" if sample.get("on_ac") else "от батареи"
        lines.append(f"Батарея: {sample['battery_pct']}% · {power}")
    if failures:
        lines.append("Свежий замер недоступен, показаны последние полученные данные.")
    return "\n".join(lines)


def run():
    data = Path(os.environ.get("BOT_DATA_DIR", "/data"))
    token = os.environ["BOT_TOKEN"]
    state_path = data / "state.json"
    print("Waiting for owner provisioning", flush=True)
    while True:
        snapshot = read_snapshot(data)
        if snapshot is not None:
            break
        time.sleep(2)
    config = {"token": token, "owner": snapshot["owner"], "offset": snapshot.get("offset", 0)}
    state = {"offset": config["offset"], "alerted": False}
    if state_path.exists():
        state = json.loads(state_path.read_text(encoding="utf-8"))
    sample, sampled_at, failures = None, None, 0
    next_sample = 0

    while True:
        try:
            if time.monotonic() >= next_sample:
                next_sample = time.monotonic() + SAMPLE_INTERVAL
                fresh = fetch_metrics(data, config["owner"], now=time.time())
                if fresh is not None:
                    sample = fresh
                    sampled_at = datetime.now(ZoneInfo("Europe/Moscow"))
                    failures = 0
                else:
                    failures += 1
                change = status_change(fresh is not None, failures, state["alerted"])
                if change is not None:
                    text = "Ноутбук снова на связи." if change == "recovered" else (
                        "Нет связи с домашним ноутбуком. Три проверки подряд не получили статистику."
                    )
                    telegram(config["token"], "sendMessage", chat_id=config["owner"], text=text)
                    state["alerted"] = change == "offline"
                    write_json(state_path, state)

            updates = telegram(
                config["token"], "getUpdates", timeout=15,
                offset=state["offset"], allowed_updates=["message"],
            )
            if not updates and state["offset"]:
                state["offset"] = 0
                write_json(state_path, state)
            for update in updates:
                if owner_command(update.get("message", {}), config["owner"]) is not None:
                    telegram(
                        config["token"], "sendMessage", chat_id=config["owner"],
                        text=format_status(sample, sampled_at, failures),
                    )
                state["offset"] = update["update_id"] + 1
                write_json(state_path, state)
        except TelegramError as error:
            print(error, file=sys.stderr, flush=True)
            time.sleep(5)


if __name__ == "__main__":
    os.umask(0o077)
    if sys.argv[1:] == ["ingest"]:
        ingest()
    else:
        run()
