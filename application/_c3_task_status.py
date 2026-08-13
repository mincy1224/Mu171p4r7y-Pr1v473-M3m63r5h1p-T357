
"""Read / write the task_status.json lifecycle file."""
import json
import os
import time

_APP_DIR = os.path.dirname(os.path.abspath(__file__))
STATUS_PATH = os.path.join(_APP_DIR, "task_status.json")

_ALLOWED_STATUS = {"unprepared", "active", "cracked"}


def read() -> dict | None:
    if not os.path.isfile(STATUS_PATH):
        return None
    try:
        with open(STATUS_PATH, "r") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(data, dict):
        return None
    if data.get("status") not in _ALLOWED_STATUS:
        return None
    return data


def write(status: str, info: str = "") -> None:
    if status not in _ALLOWED_STATUS:
        raise ValueError(f"invalid task status: {status!r}")
    data = {
        "status": status,
        "info": info,
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    tmp = STATUS_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, STATUS_PATH)
