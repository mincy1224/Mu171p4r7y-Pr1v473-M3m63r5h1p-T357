"""config.json / pretreat pre.json access."""
from __future__ import annotations

import json

from .paths import APP_DIR


def load_config() -> dict:
    with open(APP_DIR / "config.json", encoding="utf-8") as f:
        return json.load(f)


def load_preset() -> dict:
    with open(APP_DIR / "pretreat" / "pre.json", encoding="utf-8") as f:
        return json.load(f)


def load_users(kind: str) -> dict:
    """set_holder_users.json / querier_users.json → {user_id: meta}."""
    name = "set_holder_users.json" if kind == "holder" else "querier_users.json"
    with open(APP_DIR / "pretreat" / name, encoding="utf-8") as f:
        return json.load(f)


def manager_url(cfg: dict | None = None) -> str:
    cfg = cfg or load_config()
    m = cfg["manage_server"]
    return f"http://{m['server_ip']}:{m['http_port']}"


def db_path(cfg: dict | None = None) -> str:
    cfg = cfg or load_config()
    m = cfg["manage_server"]
    return str(APP_DIR / cfg["storage_root_dir"] / "manage_server" / m["db_name"])
