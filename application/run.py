
import sys
import os

from _c3_task_status import read as read_task_status, write as write_task_status
from _c3_log import info, warn, error


def _check_task_status():
    """Validate task_status.json before launching any component.
    Returns True if the caller should proceed, False if run.py should exit."""
    s = read_task_status()

    if s is None:
        write_task_status("unprepared", "task_status.json was missing or corrupted")
        warn("run", "task_status.json missing or corrupted — created with status=unprepared")
        s = read_task_status()

    status = s["status"]
    task_info = s.get("info", "")

    if status == "active":
        return True

    if status == "cracked":
        error("run", f"task is cracked — {task_info}")
        info("run", "Stop all C3 processes, run pretreat, then restart all components.")
        return False

    # status == "unprepared"
    warn("run", "Task status is 'unprepared'. The environment has not been initialised.")
    ans = input("[run] Clean task records now? (y/n) ").strip().lower()
    if ans == "y":
        info("run", "Running pretreat --onlyclr ...")
        from pretreat.pretreat import run_cli as pretreat_cli
        pretreat_cli(["--onlyclr"])
        info("run", "Cleaned. Please run 'python run.py pretreat' now.")
    else:
        warn("run", "Aborted. Run 'python run.py pretreat' first.")
    return False


def main():
    if len(sys.argv) < 2:
        print("usage: python run.py <target> [args...]")
        print("  target: manage_server | steward | peer0 | peer1 | set_holder | querier | pretreat")
        sys.exit(1)

    target = sys.argv[1]
    extra = sys.argv[2:]

    # pretreat is always allowed — it resets the task status
    if target != "pretreat":
        if not _check_task_status():
            sys.exit(1)

    if target == "manage_server":
        info("run", "launching manage_server ...")
        from manage_server.app import C3ManageServer
        C3ManageServer().run()

    elif target == "set_holder":
        info("run", "launching set_holder ...")
        from set_holder.app import C3SetHolder
        C3SetHolder.run_cli(extra)

    elif target == "querier":
        info("run", "launching querier ...")
        from querier.app import C3Querier
        C3Querier.run_cli(extra)

    elif target in ("steward", "peer0", "peer1"):
        info("run", f"launching {target} agent ...")
        from agent_server.app import C3AgentServer
        C3AgentServer(target).run()

    elif target == "pretreat":
        info("run", "launching pretreat ...")
        from pretreat.pretreat import run_cli
        run_cli(extra)

    else:
        error("run", f"unknown target: {target}")
        sys.exit(1)


if __name__ == "__main__":
    main()
