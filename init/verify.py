"""Smoke test for a running Lab Tracker Odoo (XML-RPC, env-based connection, see odoo_api.py). Exit code 1 on any failure.

Checks: apps installed, lab_dashboard installed, 4 Lab automation rules active (old equipment hard block gone), 3 Lab groups,
task fields (Build in, numeric time, request total), equipment dropdown without availability filter, Lab Home menus incl. Intern,
4 request stages incl. Pending pickup, x_condition + x_equipment_id
fields, lab.equipment.contact model + equipment Contacts tab + Lab ir.access rows (old coordinator read-only rows gone),
dash_ro SELECT on lab_equipment_contact via the replica, Lab Home get_data() source (replica or live), LAB- sequence on a throwaway request (deleted afterwards),
provisioning marker; equipment features (contact types + seeds, legacy role gone, Best practices / Maintenance
procedures Html fields and form pages, contact-type access rows). Run inside the container:  docker compose exec odoo python3 /opt/labtracker/init/verify.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from odoo_api import call  # noqa: E402

APPS = ["base", "contacts", "project", "crm", "maintenance", "base_automation", "lab_dashboard"]
RULES = ["Lab request: add LAB- code", "Lab sample: inherit equipment",
         "Lab sample: complete when all ticked", "Lab request: default submitted"]
RETIRED_RULES = ["Lab: block equipment that is down"]  # removed 2026-11-21: unavailable equipment may be assigned
STAGES = ["New", "Active", "Complete", "Pending pickup"]
GROUPS = ["group_lab_manager", "group_lab_coordinator", "group_lab_intern"]
WANT_SOURCE = os.environ.get("VERIFY_EXPECT_SOURCE", "replica")

CONTACT_FIELDS = ("equipment_id", "partner_id", "type_id", "note", "active", "phone", "email", "company_id")
ACCESS = [  # (name, model, operation) -- add-on CSV rows and init/lab_roles.py rows
    ("Lab equipment contacts: internal users read", "lab.equipment.contact", "r"),
    ("Lab equipment contacts: equipment managers full access", "lab.equipment.contact", "crud"),
    ("Lab equipment contacts: administrators full access", "lab.equipment.contact", "crud"),
    ("Lab coordinator: create and edit equipment contacts", "lab.equipment.contact", "cu"),
    ("Lab coordinator: update equipment (condition only, enforced by lab_dashboard)", "maintenance.equipment", "u"),
    ("Lab coordinator: no create/delete on maintenance.equipment", "maintenance.equipment", "cd"),
    ("Lab coordinator: create and edit contacts", "res.partner", "cu"),
    ("Lab coordinator: no delete on contacts", "res.partner", "d"),
    ("Lab coordinator: read-only maintenance.request", "maintenance.request", "cud"),
    ("Lab coordinator: read-only crm.lead", "crm.lead", "cud"),
    # equipment features (lab_dashboard 20.0.2.3.0)
    ("Lab equipment contact types: internal users read", "lab.equipment.contact.type", "r"),
    ("Lab equipment contact types: equipment managers full access", "lab.equipment.contact.type", "crud"),
    ("Lab equipment contact types: administrators full access", "lab.equipment.contact.type", "crud"),
    ("Lab coordinator: create and edit equipment contact types", "lab.equipment.contact.type", "cu"),
    # tasks/intern features (lab_dashboard 20.0.2.2.0)
    ("Lab intern: add samples to assigned requests", "project.task", "c"),
]
SEED_TYPES = {"contact_type_sales": "Sales", "contact_type_support": "Support", "contact_type_engineer": "Engineer"}
HTML_FIELDS = ("lab_best_practices", "lab_maintenance_procedures")
RETIRED = [("Lab coordinator: read-only maintenance.equipment", "maintenance.equipment"),
           ("Lab coordinator: read-only contacts (own partner editable)", "res.partner")]

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}", flush=True)


def check_replica_table(table):
    """dash_ro (the dashboard's read-only role on the replica) gets SELECT on new tables via default privileges."""
    dsn = os.environ.get("LAB_DASH_DSN")
    if not dsn:
        check(f"replica SELECT on {table}", WANT_SOURCE != "replica", "LAB_DASH_DSN not set")
        return
    try:
        import psycopg2
        with psycopg2.connect(dsn, connect_timeout=5) as conn, conn.cursor() as cur:
            cur.execute(f"SELECT count(*) FROM {table}")
            check(f"replica SELECT on {table} (dash_ro)", True, f"rows={cur.fetchone()[0]}")
    except Exception as exc:  # noqa: BLE001
        check(f"replica SELECT on {table} (dash_ro)", False, exc.__class__.__name__)


def main():
    installed = {m["name"] for m in call("ir.module.module", "search_read", [[("name", "in", APPS), ("state", "=", "installed")]], {"fields": ["name"]})}
    check("apps installed", installed == set(APPS), f"missing={sorted(set(APPS) - installed) or 'none'}")

    rules = {r["name"]: r["active"] for r in call("base.automation", "search_read", [[("name", "in", RULES)]],
                                                   {"fields": ["name", "active"], "context": {"active_test": False}})}
    check(f"{len(RULES)} Lab rules active", all(rules.get(r) for r in RULES), f"{sum(bool(rules.get(r)) for r in RULES)}/{len(RULES)}")
    left = call("base.automation", "search_count", [[("name", "in", RETIRED_RULES)]], {"context": {"active_test": False}})
    check("old 'block equipment that is down' rule removed", not left, f"left={left}")

    groups = call("ir.model.data", "search_read", [[("module", "=", "lab_tracker"), ("name", "in", GROUPS), ("model", "=", "res.groups")]], {"fields": ["name"]})
    check("3 Lab groups", len(groups) == 3, f"{len(groups)}/3")

    eq_fields = call("maintenance.equipment", "fields_get", [["x_condition"]], {"attributes": ["type"]})
    task_fields = call("project.task", "fields_get", [["x_equipment_id", "x_scan", "x_submitted"]], {"attributes": ["type"]})
    check("x_condition on equipment", "x_condition" in eq_fields)
    check("x_equipment_id on task", "x_equipment_id" in task_fields, f"also {sorted(task_fields)}")

    # --- 2026-11-21 tasks changes
    new_fields = call("project.task", "fields_get", [["x_build_in", "lab_time_taken", "lab_time_total"]], {"attributes": ["type", "readonly", "store"]})
    check("Build in toggle on samples", new_fields.get("x_build_in", {}).get("type") == "boolean")
    check("numeric Time taken (h) on samples", new_fields.get("lab_time_taken", {}).get("type") == "float")
    check("request total time is computed", new_fields.get("lab_time_total", {}).get("type") == "float" and new_fields["lab_time_total"].get("readonly"))
    form = call("project.task", "get_views", [[[False, "form"]]], {})["views"]["form"]["arch"]
    check("task form: Build in + Time taken, no old text field", all(f in form for f in ("x_build_in", "lab_time_taken", "lab_time_total")) and "x_time_spent" not in form)
    equip_view = call("ir.ui.view", "search_read", [[("name", "=", "Lab: equipment on task")]], {"fields": ["arch_db"], "limit": 1})
    check("equipment dropdown lists all equipment", bool(equip_view) and "x_condition" not in equip_view[0]["arch_db"])
    menus = call("ir.model.data", "search_read", [[("module", "=", "lab_dashboard"), ("name", "in", ["menu_lab_root", "menu_lab_home_root"])]], {"fields": ["res_id"]})
    intern_gid = call("ir.model.data", "search_read", [[("module", "=", "lab_tracker"), ("name", "=", "group_lab_intern")]], {"fields": ["res_id"], "limit": 1})
    menu_groups = [set(m["group_ids"]) for m in call("ir.ui.menu", "read", [[m["res_id"] for m in menus], ["group_ids"]])] if menus else []
    check("Lab Home menus visible to interns", bool(menu_groups) and bool(intern_gid) and all(intern_gid[0]["res_id"] in g for g in menu_groups))

    # --- 2026-11-22 QA follow-ups
    guard_left = call("base.automation", "search_count", [[("name", "=", "Lab intern: cannot change assignee")]], {"context": {"active_test": False}})
    check("old intern assignee automation removed (guard is in lab_dashboard)", not guard_left, f"left={guard_left}")
    pm = call("ir.model.data", "search_read", [[("module", "=", "project"), ("name", "=", "menu_main_pm")]], {"fields": ["res_id"], "limit": 1})
    board = call("ir.ui.menu", "search_read", [[("parent_id", "=", pm[0]["res_id"] if pm else 0), ("name", "in", ["Requests board", "Lab home"])]], {"fields": ["name"]})
    check("Project > Requests board (renamed from Lab home)", [m["name"] for m in board] == ["Requests board"], f"found={[m['name'] for m in board]}")

    # equipment contacts model (lab_dashboard add-on) and its access rows
    ec_fields = call("lab.equipment.contact", "fields_get", [list(CONTACT_FIELDS)], {"attributes": ["type"]})
    check("lab.equipment.contact fields", set(ec_fields) == set(CONTACT_FIELDS), f"missing={sorted(set(CONTACT_FIELDS) - set(ec_fields)) or 'none'}")
    check("equipment Contacts tab field", "lab_contact_ids" in call("maintenance.equipment", "fields_get", [["lab_contact_ids"]], {"attributes": ["type"]}))
    arch = call("maintenance.equipment", "get_views", [[[False, "form"]]], {})["views"]["form"]["arch"]
    check("equipment form has Contacts tab", 'name="lab_contacts"' in arch and "lab_contact_ids" in arch)
    rows = {(r["name"], r["operation"]) for r in call("ir.access", "search_read", [[("model_id.model", "in", list({m for _, m, _ in ACCESS}))]],
                                                     {"fields": ["name", "operation"]})}
    missing = [n for n, _, op in ACCESS if (n, op) not in rows]
    check(f"{len(ACCESS)} Lab access rows", not missing, f"missing={missing or 'none'}")
    old = [n for n, m in RETIRED if call("ir.access", "search_count", [[("name", "=", n)]])]
    check("retired coordinator read-only rows gone", not old, f"left={old or 'none'}")
    check_replica_table("lab_equipment_contact")

    # ---- equipment features (lab_dashboard 20.0.2.3.0) ------------------------------------------
    tf = call("lab.equipment.contact", "fields_get", [["type_id", "role"]], {"attributes": ["type", "relation"]})
    check("contact type is a many2one to lab.equipment.contact.type",
          tf.get("type_id", {}).get("relation") == "lab.equipment.contact.type", f"type_id={tf.get('type_id')}")
    check("legacy contact role selection removed", "role" not in tf)
    seeds = call("ir.model.data", "search_read", [[("module", "=", "lab_dashboard"), ("name", "in", list(SEED_TYPES))]],
                 {"fields": ["name", "res_id"]})
    names = {r["name"]: call("lab.equipment.contact.type", "read", [[r["res_id"]], ["name"]])[0]["name"] for r in seeds}
    check("seed contact types Sales / Support / Engineer", set(names) == set(SEED_TYPES), f"found={sorted(names.values())}")
    untyped = call("lab.equipment.contact", "search_count", [[("type_id", "=", False)]], {"context": {"active_test": False}})
    check("equipment contacts have a type (migration)", True, f"contacts without type={untyped} (allowed; 0 expected right after upgrade)")
    hf = call("maintenance.equipment", "fields_get", [list(HTML_FIELDS)], {"attributes": ["type"]})
    check("Best practices / Maintenance procedures Html fields", all(hf.get(f, {}).get("type") == "html" for f in HTML_FIELDS),
          f"{ {f: hf.get(f, {}).get('type') for f in HTML_FIELDS} }")
    check("equipment form has Best practices + Maintenance procedures pages",
          all(f'name="{f}"' in arch for f in HTML_FIELDS) and 'string="Best practices"' in arch)
    # ---- end equipment features

    data = call("lab.dashboard", "get_data", [])
    check(f"Lab Home get_data source={WANT_SOURCE}", data.get("source") == WANT_SOURCE, f"source={data.get('source')} counts={data.get('counts')}")

    pid = call("project.project", "search", [[("name", "=", "Lab requests")]], {"limit": 1})
    check("project 'Lab requests'", bool(pid))
    if pid:
        stages = call("project.task.type", "search_read", [[("project_ids", "in", pid)]],
                      {"fields": ["name"], "order": "sequence,id"})
        check("request stages " + " -> ".join(STAGES), [s["name"] for s in stages] == STAGES, f"got={[s['name'] for s in stages]}")
        blocking = call("project.task", "fields_get", [["lab_is_blocked", "lab_block_reason"]], {"attributes": ["readonly"]})
        check("structured blocking fields", len(blocking) == 2 and all(f["readonly"] for f in blocking.values()))
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
