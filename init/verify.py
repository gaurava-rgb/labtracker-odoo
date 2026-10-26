"""Smoke test for a running Lab Tracker Odoo (XML-RPC, env-based connection, see odoo_api.py). Exit code 1 on any failure.

Checks: apps installed, lab_dashboard installed, 5 Lab automation rules active, 3 Lab groups, x_condition + x_equipment_id
fields, Lab Home get_data() source (replica or live), LAB- sequence on a throwaway request (deleted afterwards),
provisioning marker. Run inside the container:  docker compose exec odoo python3 /opt/labtracker/init/verify.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from odoo_api import call  # noqa: E402

APPS = ["base", "contacts", "project", "crm", "maintenance", "base_automation", "lab_dashboard"]
RULES = ["Lab request: add LAB- code", "Lab sample: inherit equipment", "Lab: block equipment that is down",
         "Lab sample: complete when all ticked", "Lab request: default submitted"]
GROUPS = ["group_lab_manager", "group_lab_coordinator", "group_lab_intern"]
WANT_SOURCE = os.environ.get("VERIFY_EXPECT_SOURCE", "replica")

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}", flush=True)


def main():
    installed = {m["name"] for m in call("ir.module.module", "search_read", [[("name", "in", APPS), ("state", "=", "installed")]], {"fields": ["name"]})}
    check("apps installed", installed == set(APPS), f"missing={sorted(set(APPS) - installed) or 'none'}")

    rules = {r["name"]: r["active"] for r in call("base.automation", "search_read", [[("name", "in", RULES)]],
                                                   {"fields": ["name", "active"], "context": {"active_test": False}})}
    check("5 Lab rules active", all(rules.get(r) for r in RULES), f"{sum(bool(rules.get(r)) for r in RULES)}/5")

    groups = call("ir.model.data", "search_read", [[("module", "=", "lab_tracker"), ("name", "in", GROUPS), ("model", "=", "res.groups")]], {"fields": ["name"]})
    check("3 Lab groups", len(groups) == 3, f"{len(groups)}/3")

    eq_fields = call("maintenance.equipment", "fields_get", [["x_condition"]], {"attributes": ["type"]})
    task_fields = call("project.task", "fields_get", [["x_equipment_id", "x_scan", "x_submitted"]], {"attributes": ["type"]})
    check("x_condition on equipment", "x_condition" in eq_fields)
    check("x_equipment_id on task", "x_equipment_id" in task_fields, f"also {sorted(task_fields)}")

    data = call("lab.dashboard", "get_data", [])
    check(f"Lab Home get_data source={WANT_SOURCE}", data.get("source") == WANT_SOURCE, f"source={data.get('source')} counts={data.get('counts')}")

    pid = call("project.project", "search", [[("name", "=", "Lab requests")]], {"limit": 1})
    check("project 'Lab requests'", bool(pid))
    if pid:
        seq = call("ir.sequence", "search_read", [[("code", "=", "lab.request")]], {"fields": ["number_next_actual"], "limit": 1})
        tid = call("project.task", "create", [{"name": "verify throwaway request", "project_id": pid[0]}])
        name = call("project.task", "read", [[tid], ["name"]])[0]["name"]
        call("project.task", "unlink", [[tid]])
        if seq:  # give the consumed number back so real requests still start where they would have
            call("ir.sequence", "write", [[seq[0]["id"]], {"number_next_actual": seq[0]["number_next_actual"]}])
        check("LAB- sequence on new request", name.startswith("LAB-"), f"name={name!r} (deleted, number returned)")

    marker = call("ir.config_parameter", "search_read", [[("key", "=", "labtracker.provisioned_version")]], {"fields": ["value"]})
    check("provisioning marker", bool(marker), f"value={marker[0]['value'] if marker else None}")

    failed = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
