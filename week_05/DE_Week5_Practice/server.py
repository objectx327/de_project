"""Local training API. Run with Python 3.10+; no third-party packages needed."""
import argparse
import copy
import hashlib
import json
import os
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parent
SCENARIOS = {
    "normal", "transient_503", "rate_limit_once", "persistent_503",
    "invalid_json", "wrong_structure", "missing_cursor", "cursor_loop",
    "empty_then_data", "new_field", "timeout_once", "conflict_same_version",
}


def timestamp(value):
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("A timezone is required")
    return result.astimezone(timezone.utc)


class TrainingServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, token):
        super().__init__(address, Handler)
        self.token = token
        self.attempts = {}
        self.lock = threading.Lock()
        self.snapshots = {}
        for name in ("initial", "updated"):
            path = ROOT / "data" / f"orders_{name}.json"
            self.snapshots[name] = json.loads(path.read_text(encoding="utf-8"))["data"]


class Handler(BaseHTTPRequestHandler):
    server_version = "DE5Training/1.0"

    def log_message(self, format, *args):
        # Do not print headers or full query strings.
        pass

    def reply(self, status, payload, headers=None, raw=False):
        body = payload if raw else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            for name, value in (headers or {}).items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass  # Expected when the student tests a read timeout.

    def do_GET(self):
        url = urlsplit(self.path)
        if url.path == "/health":
            self.reply(200, {"status": "ok"})
            return
        if url.path != "/v1/orders":
            self.reply(404, {"error": "endpoint_not_found"})
            return
        if self.headers.get("Authorization") != f"Bearer {self.server.token}":
            self.reply(401, {"error": "invalid_token"})
            return
        raw_params = parse_qs(url.query, keep_blank_values=True)
        allowed = {"account_id", "updated_from", "updated_to", "limit", "cursor", "status", "snapshot", "scenario"}
        if set(raw_params) - allowed or any(len(v) != 1 for v in raw_params.values()):
            self.reply(400, {"error": "unknown_or_repeated_parameter"})
            return
        params = {k: v[0] for k, v in raw_params.items()}
        if not all(params.get(k) for k in ("account_id", "updated_from", "updated_to")):
            self.reply(400, {"error": "missing_required_parameter"})
            return
        if params["account_id"] != "shop_de":
            self.reply(403, {"error": "account_not_allowed"})
            return
        try:
            start, end = timestamp(params["updated_from"]), timestamp(params["updated_to"])
            limit = int(params.get("limit", "4"))
            if start >= end or not 1 <= limit <= 5:
                raise ValueError("Invalid period or limit")
        except (ValueError, TypeError, OverflowError):
            self.reply(400, {"error": "invalid_period_or_limit"})
            return
        snapshot = params.get("snapshot", "initial")
        scenario = params.get("scenario", "normal")
        status = params.get("status")
        if (snapshot not in self.server.snapshots or scenario not in SCENARIOS
                or (status is not None and status not in {"pending", "paid", "cancelled", "refunded"})):
            self.reply(400, {"error": "invalid_selection"})
            return
        selection = [snapshot, scenario, params["account_id"], params["updated_from"], params["updated_to"], limit, status]
        fingerprint = hashlib.sha256(json.dumps(selection).encode()).hexdigest()[:12]

        def cursor_for(offset):
            return f"c_{fingerprint}_{offset}"

        cursor = params.get("cursor")
        try:
            offset = 0 if cursor is None else int(cursor.rsplit("_", 1)[1])
            if offset < 0 or (cursor is not None and cursor != cursor_for(offset)):
                raise ValueError("Invalid cursor")
        except (ValueError, IndexError):
            self.reply(400, {"error": "invalid_cursor_for_selection"})
            return
        records = copy.deepcopy([
            row for row in self.server.snapshots[snapshot]
            if start <= timestamp(row["updated_at"]) < end
            and (status is None or row["status"] == status)
        ])
        records.sort(key=lambda row: (timestamp(row["updated_at"]), row["order_id"]))
        if cursor is not None and offset >= len(records) and not (offset == 0 and not records):
            self.reply(400, {"error": "cursor_out_of_range"})
            return
        key = (fingerprint, cursor)
        with self.server.lock:
            self.server.attempts[key] = self.server.attempts.get(key, 0) + 1
            attempt = self.server.attempts[key]
        # Error scenarios target the second data page. Use the standard period and limit=4.
        second = offset == limit and len(records) > limit
        if second and (scenario == "persistent_503" or (scenario == "transient_503" and attempt == 1)):
            self.reply(503, {"error": "temporarily_unavailable"})
            return
        if second and scenario == "rate_limit_once" and attempt == 1:
            self.reply(429, {"error": "rate_limit"}, {"Retry-After": "1"})
            return
        if second and scenario == "timeout_once" and attempt == 1:
            time.sleep(1.0)
        if second and scenario == "invalid_json":
            self.reply(200, b'{"data": [', raw=True)
            return
        if scenario == "empty_then_data" and cursor is None and records:
            self.reply(200, {"data": [], "next_cursor": cursor_for(0), "meta": {"total_count": len(records), "snapshot": snapshot}})
            return
        page = records[offset:offset + limit]
        next_cursor = cursor_for(offset + limit) if offset + limit < len(records) else None
        payload = {"data": page, "next_cursor": next_cursor, "meta": {"total_count": len(records), "snapshot": snapshot}}
        if second and scenario == "wrong_structure":
            payload["data"] = {"orders": page}
        if second and scenario == "missing_cursor":
            del payload["next_cursor"]
        if second and scenario == "cursor_loop":
            payload["next_cursor"] = cursor
        if scenario == "new_field":
            for row in page:
                row["promotion_note"] = "Дополнительное поле источника"
        if scenario == "conflict_same_version":
            from decimal import Decimal
            for row in page:
                if row["order_id"] == "000001":
                    row["items"][0]["unit_price"] = format(Decimal(row["items"][0]["unit_price"]) + 1, ".2f")
                    row["total_amount"] = format(sum(Decimal(x["unit_price"]) * x["quantity"] for x in row["items"]), ".2f")
        self.reply(200, payload)


def main():
    parser = argparse.ArgumentParser(description="Local API for DE Week 5")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    token = os.getenv("DE5_API_TOKEN")
    if not token:
        parser.error("Set DE5_API_TOKEN before starting the server")
    server = TrainingServer(("127.0.0.1", args.port), token)
    print(f"Training API: http://127.0.0.1:{server.server_port}/v1/orders", flush=True)
    print("Stop: Ctrl+C. Restart resets transient error counters.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
