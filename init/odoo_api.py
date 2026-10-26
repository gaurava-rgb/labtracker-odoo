"""Small Odoo XML-RPC helper for the Lab Tracker provisioning scripts.

Connection settings come from the environment only (no .env file reads, no fixed paths):
  ODOO_URL        default http://localhost:8069
  ODOO_DB         default labtracker
  ODOO_LOGIN      default admin
  ODOO_PASSWORD   falls back to ADMIN_PASSWORD (the value docker-compose gives the odoo container)
The password is never printed. Rate-limit (HTTP 429) replies are retried with a back-off.

Usage from Python:   from odoo_api import call;  call('res.partner', 'search_read', [[]], {'fields': ['name'], 'limit': 5})
Usage from a shell:  python3 odoo_api.py res.partner search_read '[[]]' '{"fields": ["name"], "limit": 5}'
"""
import os
import time
import xmlrpc.client

URL = os.environ.get("ODOO_URL", "http://localhost:8069").rstrip("/")
DB = os.environ.get("ODOO_DB", "labtracker")
LOGIN = os.environ.get("ODOO_LOGIN", "admin")
_PASSWORD = os.environ.get("ODOO_PASSWORD") or os.environ.get("ADMIN_PASSWORD")

READ_METHODS = ("search", "search_read", "read", "search_count", "fields_get", "name_get")
PACE = float(os.environ.get("ODOO_RPC_PACE", "0"))  # seconds to sleep after each write (0 on a self-hosted box)

_uid = None
_models = xmlrpc.client.ServerProxy(f"{URL}/xmlrpc/2/object", allow_none=True)


def _password():
    if not _PASSWORD:
        raise SystemExit("ODOO_PASSWORD (or ADMIN_PASSWORD) is not set")
    return _PASSWORD


def uid():
    """Authenticate once and cache the user id."""
    global _uid
    if _uid is None:
        common = xmlrpc.client.ServerProxy(f"{URL}/xmlrpc/2/common", allow_none=True)
        _uid = common.authenticate(DB, LOGIN, _password(), {})
        if not _uid:
            raise SystemExit(f"Odoo login failed for user '{LOGIN}' on {URL} db '{DB}'")
    return _uid


def call(model, method, args=None, kwargs=None):
    for attempt in range(7):
        try:
            res = _models.execute_kw(DB, uid(), _password(), model, method, args or [], kwargs or {})
            if PACE and method not in READ_METHODS:
                time.sleep(PACE)
            return res
        except xmlrpc.client.ProtocolError as e:
            if e.errcode != 429 or attempt == 6:
                raise
            time.sleep(4 * (attempt + 1))


if __name__ == "__main__":
    import json
    import sys

    model, method = sys.argv[1], sys.argv[2]
    args = json.loads(sys.argv[3]) if len(sys.argv) > 3 else []
    kwargs = json.loads(sys.argv[4]) if len(sys.argv) > 4 else {}
    print(json.dumps(call(model, method, args, kwargs), indent=1, default=str))
