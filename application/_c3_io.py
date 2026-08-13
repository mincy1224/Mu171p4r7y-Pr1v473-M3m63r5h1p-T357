
import json
import os


def read_json(path: str) -> dict:
    if not os.path.isfile(path):
        raise FileNotFoundError(f"missing file: {path}")
    with open(path) as f:
        return json.load(f)


def ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)
