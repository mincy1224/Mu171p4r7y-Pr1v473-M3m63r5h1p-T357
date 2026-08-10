# draft
import sys


def main():
    if len(sys.argv) < 2:
        print("usage: python run.py <target> [args...]")
        print("  target: manage_server | steward | peer0 | peer1 | set_holder | querier | pretreat")
        sys.exit(1)

    target = sys.argv[1]
    extra = sys.argv[2:]

    if target == "manage_server":
        from manage_server.app import C3ManageServer
        C3ManageServer().run()

    elif target == "set_holder":
        from set_holder.app import C3SetHolder
        C3SetHolder.run_cli(extra)

    elif target == "querier":
        from querier.app import C3Querier
        C3Querier.run_cli(extra)

    elif target in ("steward", "peer0", "peer1"):
        from agent_server.app import C3AgentServer
        C3AgentServer(target).run()

    elif target == "pretreat":
        from pretreat.pretreat import run_cli
        run_cli(extra)

    else:
        print(f"unknown target: {target}")
        sys.exit(1)


if __name__ == "__main__":
    main()
