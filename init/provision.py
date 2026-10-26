"""Provision a running Lab Tracker Odoo over XML-RPC. Idempotent; run on every container boot by labtracker-entrypoint.sh.

Steps (all search-before-create):
  1. wait until Odoo answers on ODOO_URL
  2. skip everything if ir.config_parameter 'labtracker.provisioned_version' == PROVISION_VERSION (set FORCE_PROVISION=1 to override)
  3. lab_rules.main()    project "Lab requests" + stages, LAB- sequence + rule, Samples wording, x_equipment_id, x_condition, Lab home filters
  4. lab_phase2.main()   sample / request fields, views, two more rules, stage cleanup
  5. lab_roles.main()    Lab privilege, Manager / Coordinator / Intern groups, access rows, intern guard, menu visibility
  6. lab_rules.ensure_labels() again (registry reloads reset base-field labels) + trim_menus()
  7. write the marker parameter
Bump PROVISION_VERSION whenever one of the lab_*.py scripts changes so existing databases get re-provisioned on the next boot.
Never prints any secret.
"""
import os
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from odoo_api import URL, call  # noqa: E402
import lab_phase2  # noqa: E402
import lab_roles  # noqa: E402
import lab_rules  # noqa: E402

PROVISION_VERSION = "2026.10.25-1"
PARAM = "labtracker.provisioned_version"
WAIT_SECONDS = int(os.environ.get("PROVISION_WAIT_SECONDS", "900"))


def log(msg):
    print(f"[labtracker provision] {time.strftime('%H:%M:%S')} {msg}", flush=True)


def wait_for_odoo():
    deadline = time.time() + WAIT_SECONDS
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"{URL}/web/health", timeout=5) as r:
                if r.status == 200:
                    return
        except Exception:  # noqa: BLE001 - not up yet
            pass
        time.sleep(3)
    raise SystemExit(f"Odoo did not answer on {URL} within {WAIT_SECONDS}s")


def get_param(key):
    rec = call("ir.config_parameter", "search_read", [[("key", "=", key)]], {"fields": ["value"], "limit": 1})
    return rec[0]["value"] if rec else None


def set_param(key, value):
    ids = call("ir.config_parameter", "search", [[("key", "=", key)]], {"limit": 1})
    if ids:
        call("ir.config_parameter", "write", [ids, {"value": value}])
    else:
        call("ir.config_parameter", "create", [{"key": key, "value": value}])


def main():
    wait_for_odoo()
    current = get_param(PARAM)
    if current == PROVISION_VERSION and os.environ.get("FORCE_PROVISION") != "1":
        log(f"already provisioned (version {current}); nothing to do")
        return
    log(f"provisioning: installed version {current!r} -> {PROVISION_VERSION}")
    t0 = time.time()
    log("step 1/4 lab_rules (project, sequence, fields, views, filters)")
    lab_rules.main()
    log("step 2/4 lab_phase2 (sample/request fields, rules, stages)")
    lab_phase2.main()
    log("step 3/4 lab_roles (groups, access rows, menus)")
    lab_roles.main(do_verify=False)
    log("step 4/4 labels + menu trim")
    print("labels:", lab_rules.ensure_labels())
    print("trim menus:", lab_rules.trim_menus())
    set_param(PARAM, PROVISION_VERSION)
    log(f"done in {time.time() - t0:.0f}s; marker {PARAM}={PROVISION_VERSION}")


if __name__ == "__main__":
    main()
