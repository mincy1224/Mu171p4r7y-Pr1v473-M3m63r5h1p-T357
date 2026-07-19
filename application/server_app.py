"""MPMT server entry-point — Flask HTTP + persistent Rep3 ring.

Usage::

    python application/server_app.py --party 0 --http-port 5000 \\
        --prev-port 14000 --next-host 127.0.0.1 --next-port 14001

@author  mincy
"""

import argparse
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from flask import Flask
import mpmt
from mpmt.protocol_handler import ProtocolHandler
from mpmt.channels import _build_rep3_channels
from application.config import ServerConfig
from application.server_routes import make_blueprint


def create_app(config: ServerConfig) -> Flask:
    """Build the Flask application for one server."""
    app = Flask(__name__)

    # ——— Persistent Rep3 ring channels ———
    ch_rep3 = _build_rep3_channels(
        prev_port=config.prev_port,
        next_host=config.next_host,
        next_port=config.next_port,
        party_id=config.party_id,
    )

    # ——— Server class (Composition Root) ———
    if config.party_id == 0:
        server = mpmt.MpmtServerLeader(
            set_size=config.set_size,
            fpr_mantissa=config.fpr_mantissa,
            fpr_exponent=config.fpr_exponent,
            max_holders=config.max_holders,
            ch_prev=ch_rep3["prev"],
            ch_next=ch_rep3["next"],
            hash_seeds=config.hash_seeds if config.hash_seeds else None,
        )
    else:
        server = mpmt.MpmtServerHelper(
            server_id=config.party_id,
            set_size=config.set_size,
            fpr_mantissa=config.fpr_mantissa,
            fpr_exponent=config.fpr_exponent,
            max_holders=config.max_holders,
            ch_prev=ch_rep3["prev"],
            ch_next=ch_rep3["next"],
        )

    # ——— Protocol handler (single entry point) ———
    handler = server.handler

    # ——— Routes ———
    bp = make_blueprint(server, handler, config.party_id)
    app.register_blueprint(bp)

    print(f"[server] Party {config.party_id} listening on :{config.http_port}"
          f"  (prev={config.prev_port}, next={config.next_host}:{config.next_port})")

    return app


def main():
    parser = argparse.ArgumentParser(description="MPMT server")
    parser.add_argument("--party", type=int, required=True,
                        choices=[0, 1, 2], help="Rep3 party id")
    parser.add_argument("--http-port", type=int, default=5000)
    parser.add_argument("--set-size", type=int, default=2 ** 10)
    parser.add_argument("--fpr-mantissa", type=float, default=1.0)
    parser.add_argument("--fpr-exponent", type=int, default=-3)
    parser.add_argument("--max-holders", type=int, default=32)
    parser.add_argument("--prev-port", type=int, required=True)
    parser.add_argument("--next-host", type=str, default="127.0.0.1")
    parser.add_argument("--next-port", type=int, required=True)
    args = parser.parse_args()

    config = ServerConfig(
        party_id=args.party,
        http_port=args.http_port,
        set_size=args.set_size,
        fpr_mantissa=args.fpr_mantissa,
        fpr_exponent=args.fpr_exponent,
        max_holders=args.max_holders,
        prev_port=args.prev_port,
        next_host=args.next_host,
        next_port=args.next_port,
    )

    app = create_app(config)
    app.run(host="0.0.0.0", port=config.http_port, debug=False)


if __name__ == "__main__":
    main()
