
"""
Preprocessing — generate preset material for C3 AgentServers.

Produces:
  pre.json                  — protocol params + hash_seed_list
  querier_users.json        — pre-allocated querier user_ids
  set_holder_users.json     — pre-allocated set-holder user_ids
  storage/set_holder_<user_id>/set.npy — split datasets
"""

import json
import os
import random
import secrets
import shutil
import sys
import time

import numpy as np

import mpmt

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from _c3_io import ensure_dir, read_json
from _c3_task_status import read as read_task_status, write as write_task_status

FPR_EXPONENT    = -3          # false-positive rate exponent
FPR_MANTISSA    = 1           # false-positive rate mantissa
SET_SIZE        = 1_000_000   # expected set size
PAYLOAD_PERCENT = 0.8         # actual payload ratio: real set = set_size * payload_percent
SET_HOLDER_NUM  = 8          # number of set holders
QUERIER_NUM     = 2           # number of queriers

def _c3_make_user_id() -> str:
    rand_hex = secrets.token_hex(32)           # 32 bytes = 256 bits
    ts = time.strftime("%Y%m%d%H%M%S")
    return f"{rand_hex}_{ts}"


def _c3_clean_storage(storage_dir: str) -> None:
    """Remove only protocol-created directories, leave user files untouched."""
    if not os.path.isdir(storage_dir):
        return
    # only delete directories that are part of the protocol deployment
    _KNOWN_DIRS = {"manage_server", "steward", "peer0", "peer1"}
    for name in os.listdir(storage_dir):
        path = os.path.join(storage_dir, name)
        if not os.path.isdir(path):
            continue
        if name in _KNOWN_DIRS or name.startswith("set_holder_"):
            shutil.rmtree(path)


def _c3_generate_fake_set(size: int) -> list[str]:
    return [secrets.token_hex(16) for _ in range(size)]


def _c3_split_and_save(full_set: list[str], user_ids: list[str],
                    storage_dir: str) -> None:
    n = len(user_ids)
    base = len(full_set) // n
    sizes: list[int] = []
    remaining = len(full_set)

    for i in range(n):
        if i == n - 1:
            sizes.append(remaining)
        else:
            delta = random.randint(-base // 10, base // 10)
            size = max(1, base + delta)
            size = min(size, remaining - (n - i - 1))
            sizes.append(size)
            remaining -= size

    idx = 0
    for uid, size in zip(user_ids, sizes):
        subset = full_set[idx:idx + size]
        idx += size
        out_dir = os.path.join(storage_dir, f"set_holder_{uid}")
        ensure_dir(out_dir)
        np.save(os.path.join(out_dir, "set.npy"), np.array(subset))

def _c3_clean_pretreat(pretreatment_dir: str, storage_dir: str) -> None:
    _c3_clean_storage(storage_dir)
    for name in ("pre.json", "querier_users.json", "set_holder_users.json"):
        path = os.path.join(pretreatment_dir, name)
        if os.path.isfile(path):
            os.remove(path)


def generate(force: bool = False) -> None:
    # safety: refuse to overwrite a running task unless --force
    s = read_task_status()
    if s and s["status"] == "active" and not force:
        print("[pretreat] ERROR: task is active — processes may still be running.")
        print("[pretreat] Stop all C3 processes first, then re-run with --force.")
        sys.exit(1)

    write_task_status("unprepared", "pretreatment in progress")
    pretreatment_dir = os.path.dirname(os.path.abspath(__file__))
    app_dir = os.path.dirname(pretreatment_dir)
    storage_root = read_json(os.path.join(app_dir, "config.json"))["storage_root_dir"]
    storage_dir = os.path.join(app_dir, storage_root)

    # 1. clean — WARNING: this deletes all previous protocol state,
    # including Agent storage, SQLite DB, and user datasets.
    # Only run this in a test/benchmark environment.
    print("[pretreat] WARNING: removing protocol-created directories (manage/agent/set_holder) ...")
    _c3_clean_pretreat(pretreatment_dir, storage_dir)

    # 2. compute hash-function count
    _, _, hf_num, _ = mpmt.bf_param(SET_SIZE, FPR_MANTISSA, FPR_EXPONENT)

    # 3. generate hash seeds (one 128-bit key per hash function)
    # get_key_128bits() returns bytes — store as hex so JSON can serialize
    hash_seed_list = [mpmt.get_key_128bits().hex() for _ in range(hf_num)]

    # 4. write pre.json
    ensure_dir(pretreatment_dir)
    pre = {
        "set_size":       SET_SIZE,
        "fpr_mantissa":   FPR_MANTISSA,
        "fpr_exponent":   FPR_EXPONENT,
        "hash_seed_list": hash_seed_list,
    }
    with open(os.path.join(pretreatment_dir, "pre.json"), "w") as f:
        json.dump(pre, f, indent=2)
    print(f"[pre.json]       written — set_size={SET_SIZE}, hf_num={hf_num}")

    # 5. allocate set-holder user_ids + directories
    set_holder_users: dict[str, dict] = {}
    for _ in range(SET_HOLDER_NUM):
        uid = _c3_make_user_id()
        set_holder_users[uid] = {"created_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
        ensure_dir(os.path.join(storage_dir, f"set_holder_{uid}"))

    path_sh = os.path.join(pretreatment_dir, "set_holder_users.json")
    with open(path_sh, "w") as f:
        json.dump(set_holder_users, f, indent=2)
    print(f"[set_holder]    {SET_HOLDER_NUM} user_ids written")

    # 6. allocate querier user_ids
    querier_users: dict[str, dict] = {}
    for _ in range(QUERIER_NUM):
        uid = _c3_make_user_id()
        querier_users[uid] = {"created_at": time.strftime("%Y-%m-%dT%H:%M:%S")}

    path_qr = os.path.join(pretreatment_dir, "querier_users.json")
    with open(path_qr, "w") as f:
        json.dump(querier_users, f, indent=2)
    print(f"[querier]       {QUERIER_NUM} user_ids written")

    # 7. generate & split fake dataset
    actual_size = int(SET_SIZE * PAYLOAD_PERCENT)
    full_set = _c3_generate_fake_set(actual_size)
    _c3_split_and_save(full_set, list(set_holder_users.keys()), storage_dir)
    print(f"[dataset]       {actual_size} elements split across {SET_HOLDER_NUM} holders")

    write_task_status("active")
    print("[task_status]   written status=active")


def run_cli(args: list[str] | None = None) -> None:
    pretreatment_dir = os.path.dirname(os.path.abspath(__file__))
    app_dir = os.path.dirname(pretreatment_dir)
    storage_root = read_json(os.path.join(app_dir, "config.json"))["storage_root_dir"]
    storage_dir = os.path.join(app_dir, storage_root)

    if args is None:
        args = []
    force = "--force" in args or "-f" in args

    if "--onlyclr" in args:
        s = read_task_status()
        if s and s["status"] == "active" and not force:
            print("[pretreat] ERROR: task is active — processes may still be running.")
            print("[pretreat] Stop all C3 processes first, then re-run with --force.")
            sys.exit(1)
        write_task_status("unprepared", "protocol storage was cleared; pretreat required")
        print("[onlyclr] WARNING: removing protocol-created directories (manage/agent/set_holder) ...")
        _c3_clean_pretreat(pretreatment_dir, storage_dir)
        print("[onlyclr] done")
    else:
        generate(force=force)


if __name__ == "__main__":
    run_cli(sys.argv[1:])
