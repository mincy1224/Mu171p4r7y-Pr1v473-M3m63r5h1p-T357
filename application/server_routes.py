"""Flask blueprint — HTTP routes for /reserve, /connect, /aggregate, /query.

@author  mincy
"""

import socket as _socket

from flask import Blueprint, request, jsonify

import mpmt
from mpmt.protocol_handler import ProtocolHandler


def _alloc_port() -> int:
    s = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
    s.bind(("0.0.0.0", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def make_blueprint(server, handler: ProtocolHandler, party_id: int) -> Blueprint:
    """Create a Flask blueprint with the API routes.

    Parameters
    ----------
    server :
        ``MpmtServerLeader`` or ``MpmtServerHelper`` instance.
    handler : ProtocolHandler
    party_id : int
        0 = Leader, 1 = Helper A, 2 = Helper B.
    """

    bp = Blueprint("mpmt", __name__, url_prefix="/api/v1")

    # ——— POST /reserve ————————————————————————————————————————————

    @bp.route("/reserve", methods=["POST"])
    def reserve():
        """Phase 1: 3-way confirm + port allocation.

        Request:  {"token": "<hex>", "action": "join|update|quit"}
        Response: {"status": "ok", "port": <ephemeral_port>}
        """
        body = request.get_json(force=True)
        token = bytes.fromhex(body["token"])
        action = body["action"]

        if action not in ("join", "update", "quit"):
            return jsonify({"status": "error",
                            "message": f"unknown action: {action}"}), 400

        # ——— Domain: 3-way confirm ———
        try:
            handler.three_way_confirm()
        except Exception as e:
            return jsonify({"status": "error", "message": str(e)}), 500

        # ——— Domain: tree cache mutation ———
        try:
            if action == "join":
                handler.prepare_join(token)
            elif action in ("update", "quit"):
                handler.check_token(token, action)
        except ValueError as e:
            return jsonify({"status": "error", "message": str(e)}), 409

        if action == "quit":
            handler.do_quit(token)
            return jsonify({"status": "ok"})

        # ——— Application: allocate port ———
        port = _alloc_port()
        return jsonify({"status": "ok", "port": port})

    # ——— POST /connect ————————————————————————————————————————————

    @bp.route("/connect", methods=["POST"])
    def connect():
        """Phase 2: TCP data exchange with SetHolder.

        Request:  {"token": "<hex>", "action": "join|update",
                   "port": <this_server_listen_port>}
        """
        body = request.get_json(force=True)
        action = body["action"]

        if action not in ("join", "update"):
            return jsonify({"status": "error",
                            "message": f"unknown action: {action}"}), 400

        try:
            if party_id == 0:
                handler.connect_leader(listen_port=body["port"])
            else:
                handler.connect_helper(port=body["port"])
        except Exception as e:
            return jsonify({"status": "error", "message": str(e)}), 500

        return jsonify({"status": "ok"})

    # ——— POST /aggregate ——————————————————————————————————————————

    @bp.route("/aggregate", methods=["POST"])
    def aggregate():
        handler.aggregate()
        return jsonify({"status": "ok"})

    # ——— POST /query ———————————————————————————————————————————————

    @bp.route("/query", methods=["POST"])
    def query():
        """Execute a privacy-preserving membership test.

        Request:  {"element": "<hex>", "port": <this_server_listen_port>}
        Response: {"status": "ok"}
        """
        body = request.get_json(force=True)
        element = bytes.fromhex(body["element"])
        port = body.get("port", 0)

        try:
            ch = mpmt.Channel(port)
            handler.query(element=element, ch_querier=ch,
                          hash_seeds=server.hash_seed_list)
        except Exception as e:
            return jsonify({"status": "error", "message": str(e)}), 500

        return jsonify({"status": "ok"})

    return bp

    return bp
