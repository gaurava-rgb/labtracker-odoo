"""OPTIONAL demo data: copy the Lab Tracker demo rows (Supabase Postgres) into any Lab Tracker Odoo. Re-runnable.
Not part of the Docker image. Needs:  pip install "psycopg[binary]"
  LAB_DATABASE_URL=postgres://...   ODOO_URL=http://localhost:8069 ODOO_DB=labtracker ODOO_PASSWORD=...  python3 tools/load_demo_data.py

Postgres is read-only here. Odoo 20 maintenance.equipment has no location field (goes in note);
maintenance.request has no request_date (schedule_date + close_date = done_on). Every Odoo record is looked up first (by ref / serial / name)
and tagged "LabTracker import" so re-runs update instead of duplicating.
If the custom fields from the provisioning (init/lab_rules.py) already exist (maintenance.equipment.x_condition, project.task.x_equipment_id)
they are filled too (on a labtracker-odoo stack they always do). No record ids are hardcoded. Never prints any secret.
"""
import html
import os
import pathlib
import re
import sys

import psycopg

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "init"))
from odoo_api import URL, call  # noqa: E402  (env-based connection, retries 429)

MARK = "LabTracker import"
NO_ARCHIVE = {"context": {"active_test": False}}


# ---------------------------------------------------------------- Postgres
def _db_url():
    url = os.environ.get("LAB_DATABASE_URL")
    if not url:
        raise SystemExit("set LAB_DATABASE_URL (Postgres URL of the demo data source)")
    return url


def pg_connect():
    """Direct Supabase hosts are IPv6-only; fall back to the IPv4 session pooler."""
    url = _db_url()
    try:
        return psycopg.connect(url, connect_timeout=8)
    except psycopg.OperationalError as e:
        first_err = str(e).splitlines()[0]
    m = re.match(r"postgres(?:ql)?://([^:]+):([^@]+)@([^:/]+)(?::(\d+))?/([^?]*)", url)
    if not m or not m.group(3).endswith(".supabase.co"):
        raise SystemExit(f"Postgres connect failed: {first_err}")
    user, pw, host, _port, db = m.groups()
    ref = host.split(".")[1]
    ipv6 = "2600:1f11" in first_err  # AWS ca-central-1 prefix seen in the error
    regions = ["ca-central-1"] if ipv6 else ["ca-central-1", "us-east-1", "us-west-1", "eu-central-1"]
    for reg in regions:
        for pre in ("aws-0", "aws-1"):
            try:
                return psycopg.connect(host=f"{pre}-{reg}.pooler.supabase.com", port=5432,
                                       user=f"{user}.{ref}", password=pw, dbname=db or "postgres",
                                       connect_timeout=8)
            except psycopg.OperationalError:
                continue
    raise SystemExit(f"Postgres connect failed (direct: {first_err}; pooler fallback also failed)")


def rows(cur, sql):
    cur.execute(sql)
    cols = [d.name for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def read_lab():
    with pg_connect() as conn, conn.cursor() as cur:
        return {
            "clients": rows(cur, "select * from clients order by id"),
            "staff": rows(cur, "select * from staff order by id"),
            "equipment": rows(cur, "select * from equipment order by id"),
            "tips": rows(cur, "select * from equipment_tips order by equipment_id, id"),
            "maint": rows(cur, "select * from equipment_maintenance order by equipment_id, done_on, id"),
            "requests": rows(cur, "select * from requests order by id"),
            "samples": rows(cur, "select * from samples order by request_id, id"),
        }


# ---------------------------------------------------------------- Odoo helpers
def esc(v):
    return html.escape("" if v is None else str(v))


def upsert(model, domain, vals, kwargs=None):
    """Find one record by domain (archived included) and write, else create. Returns id."""
    kw = dict(NO_ARCHIVE)
    ids = call(model, "search", [domain], {**kw, "limit": 1})
    if ids:
        call(model, "write", [ids, vals])
        return ids[0]
    return call(model, "create", [vals])


def has_field(model, name):
    return name in call(model, "fields_get", [[name]], {"attributes": ["type"]})


CONDITION_MAP = {"available": "available", "under repair": "under_repair", "out of service": "out_of_service"}


def condition_key(label):
    return CONDITION_MAP.get((label or "").strip().lower(), "available")


def get_or_create(model, name, extra=None):
    ids = call(model, "search", [[("name", "=", name)]], {**NO_ARCHIVE, "limit": 1})
    if ids:
        return ids[0]
    return call(model, "create", [{"name": name, **(extra or {})}])


# ---------------------------------------------------------------- loaders
def load_partners(data, partner_tag):
    client_ids, staff_ids = {}, {}
    for c in data["clients"]:
        comment = (f"<p><b>Contact:</b> {esc(c['contact'])}<br/><b>Billing:</b> {esc(c['billing'])}"
                   f"<br/><i>{MARK} · client {esc(c['code'])}</i></p>")
        vals = {"name": c["name"], "ref": c["code"], "is_company": True, "email": c["email"] or False,
                "comment": comment, "active": bool(c["active"]), "category_id": [(4, partner_tag)]}
        client_ids[c["id"]] = upsert("res.partner", [("ref", "=", c["code"])], vals)
        # Odoo 20 recomputes is_company on create (drops True); a plain write keeps it.
        call("res.partner", "write", [[client_ids[c["id"]]], {"is_company": True}])
    for s in data["staff"]:
        comment = (f"<p>{esc(s['description'])}<br/><b>Role:</b> {esc(s['user_type'])}"
                   f"<br/><i>{MARK} · staff {esc(s['code'])}</i></p>")
        vals = {"name": s["name"], "ref": s["code"], "is_company": False, "comment": comment,
                "active": bool(s["active"]), "category_id": [(4, partner_tag)]}
        staff_ids[s["name"]] = upsert("res.partner", [("ref", "=", s["code"])], vals)
    return client_ids, staff_ids


def load_equipment(data, category_id):
    tips_by_eq = {}
    for t in data["tips"]:
        tips_by_eq.setdefault(t["equipment_id"], []).append(t)
    eq_ids = {}
    with_cond = has_field("maintenance.equipment", "x_condition")
    for e in data["equipment"]:
        tips_html = "".join(f"<li><b>{esc(t['title'])}:</b> {esc(t['detail'])}</li>" for t in tips_by_eq.get(e["id"], []))
        note = (f"<p><b>Code:</b> {esc(e['code'])}<br/><b>Location:</b> {esc(e['location'])}"
                f"<br/><b>Condition:</b> {esc(e['condition'])}</p>"
                + (f"<p><b>Tips</b></p><ul>{tips_html}</ul>" if tips_html else "")
                + f"<p><i>{MARK}</i></p>")
        vals = {"name": e["name"], "serial_no": e["code"], "category_id": category_id,
                "note": note, "active": bool(e["active"]), "equipment_assign_to": "other"}
        if with_cond:
            vals["x_condition"] = condition_key(e["condition"])
        eq_ids[e["id"]] = upsert("maintenance.equipment", [("serial_no", "=", e["code"])], vals)
    return eq_ids


def load_maintenance(data, eq_ids, done_stage, team_id):
    n = 0
    for m in data["maint"]:
        eq = eq_ids.get(m["equipment_id"])
        if not eq:
            continue
        name = f"{m['kind']}: {m['note']}"[:120] if m["note"] else m["kind"]
        done_on = m["done_on"].isoformat()
        vals = {"name": name, "equipment_id": eq, "schedule_date": done_on, "close_date": done_on,
                "maintenance_team_id": team_id, "stage_id": done_stage,
                "maintenance_type": "corrective" if "repair" in m["kind"].lower() else "preventive",
                "description": f"<p>{esc(m['note'])}</p><p><i>{MARK} · log #{m['id']} by {esc(m['created_by'])}</i></p>"}
        marker = f"{MARK} · log #{m['id']} by"
        rid = upsert("maintenance.request", [("equipment_id", "=", eq), ("description", "ilike", marker)], vals)
        # Odoo recomputes close_date whenever stage_id is written; set it again on its own.
        call("maintenance.request", "write", [[rid], {"close_date": done_on}])
        n += 1
    return n


STATUS_TO_STAGE = {"New": "New", "Active": "Active", "Blocked": "Blocked", "Complete": "Complete"}


def load_project():
    pid = get_or_create("project.project", "Lab requests",
                        {"description": f"<p>{MARK}: requests and samples from the Lab Tracker demo.</p>"})
    stages = {}
    for seq, name in enumerate(["New", "Active", "Blocked", "Complete"], start=1):
        ids = call("project.task.type", "search", [[("name", "=", name), ("project_ids", "in", [pid])]], {"limit": 1})
        if ids:
            stages[name] = ids[0]
        else:
            stages[name] = call("project.task.type", "create",
                                [{"name": name, "sequence": seq, "fold": name == "Complete", "project_ids": [(4, pid)]}])
    return pid, stages


def find_user(name):
    if not name or name.lower() == "unassigned":
        return None
    ids = call("res.users", "search", [[("name", "=ilike", name)]], {"limit": 1})
    return ids[0] if ids else None


def load_requests(data, pid, stages, client_ids, task_tag):
    eq_name = {e["id"]: e["name"] for e in data["equipment"]}
    task_ids = {}
    for r in data["requests"]:
        user = find_user(r["assignee"])
        desc = ("<p>"
                f"<b>Submitted:</b> {esc(r['submitted'])}<br/>"
                f"<b>Due:</b> {esc(r['due'])}<br/>"
                f"<b>Equipment:</b> {esc(eq_name.get(r['equipment_id'], ''))}<br/>"
                f"<b>Assignee:</b> {esc(r['assignee'])}<br/>"
                f"<b>Save location:</b> {esc(r['save_location'])}<br/>"
                f"<b>Status (source):</b> {esc(r['status'])}</p>"
                f"<p><b>Notes</b><br/>{esc(r['notes'])}</p><p><i>{MARK} · {esc(r['code'])}</i></p>")
        vals = {"name": f"{r['code']} · {r['title']}", "project_id": pid, "partner_id": client_ids.get(r["client_id"]) or False,
                "date_deadline": r["due"].isoformat() if r["due"] else False,
                "stage_id": stages[STATUS_TO_STAGE.get(r["status"], "New")], "description": desc,
                "tag_ids": [(4, task_tag)], "user_ids": [(6, 0, [user])] if user else [(5, 0, 0)],
                "state": "1_done" if r["status"] == "Complete" else ("04_waiting_normal" if r["status"] == "Blocked" else "01_in_progress")}
        task_ids[r["id"]] = upsert("project.task",
                                   [("project_id", "=", pid), ("name", "=like", f"{r['code']} · %"), ("parent_id", "=", False)], vals)
    return task_ids


def load_samples(data, pid, stages, task_ids, task_tag):
    n = 0
    for s in data["samples"]:
        parent = task_ids.get(s["request_id"])
        if not parent:
            continue
        done = bool(s["scan"] and s["export"] and s["email"])
        flag = lambda b: "yes" if b else "no"  # noqa: E731
        desc = (f"<p><b>Scan:</b> {flag(s['scan'])}<br/><b>Export:</b> {flag(s['export'])}<br/>"
                f"<b>Email:</b> {flag(s['email'])}<br/><b>Time spent:</b> {esc(s['time_spent'])}</p>"
                f"<p><i>{MARK} · sample #{s['id']}</i></p>")
        vals = {"name": s["name"], "project_id": pid, "parent_id": parent, "description": desc,
                "stage_id": stages["Complete"] if done else stages["Active"],
                "state": "1_done" if done else "01_in_progress", "active": bool(s["active"]),
                "tag_ids": [(4, task_tag)]}
        upsert("project.task", [("parent_id", "=", parent), ("name", "=", s["name"])], vals)
        n += 1
    return n


def backfill_equipment(data, task_ids, eq_ids):
    """requests.equipment_id -> maintenance.equipment (by serial_no, via eq_ids); samples inherit the parent's.
    The 'block equipment that is down' rule is paused while writing so historic links to under-repair gear load."""
    if not has_field("project.task", "x_equipment_id"):
        return "skipped (x_equipment_id not yet created; run odoo_lab_rules.py then re-run)"
    rule = call("base.automation", "search", [[("name", "=", "Lab: block equipment that is down"), ("active", "=", True)]])
    if rule:
        call("base.automation", "write", [rule, {"active": False}])
    n_req = n_samp = 0
    try:
        for r in data["requests"]:
            tid, eq = task_ids.get(r["id"]), eq_ids.get(r["equipment_id"])
            if not tid:
                continue
            call("project.task", "write", [[tid], {"x_equipment_id": eq or False}])
            n_req += 1
            kids = call("project.task", "search", [[("parent_id", "=", tid)]], NO_ARCHIVE)
            if kids:
                call("project.task", "write", [kids, {"x_equipment_id": eq or False}])
                n_samp += len(kids)
    finally:
        if rule:
            call("base.automation", "write", [rule, {"active": True}])
    return f"{n_req} requests, {n_samp} samples"


# ---------------------------------------------------------------- main
def main():
    data = read_lab()
    print("Postgres rows:", {k: len(v) for k, v in data.items()})

    partner_tag = get_or_create("res.partner.category", MARK)
    task_tag = get_or_create("project.tags", MARK)
    client_ids, staff_ids = load_partners(data, partner_tag)

    category_id = get_or_create("maintenance.equipment.category", "Lab instruments")
    eq_ids = load_equipment(data, category_id)
    done_stage = call("maintenance.stage", "search", [[("name", "=", "Repaired")]], {"limit": 1})
    team = call("maintenance.team", "search", [[]], {"limit": 1})
    n_maint = load_maintenance(data, eq_ids, done_stage[0] if done_stage else False, team[0] if team else False)

    pid, stages = load_project()
    task_ids = load_requests(data, pid, stages, client_ids, task_tag)
    n_samples = load_samples(data, pid, stages, task_ids, task_tag)
    print("equipment backfill:", backfill_equipment(data, task_ids, eq_ids))

    # ---- verify
    counts = [
        ("res.partner (clients, tagged)", call("res.partner", "search_count",
                                               [[("category_id", "=", partner_tag), ("is_company", "=", True)]], NO_ARCHIVE)),
        ("res.partner (staff, tagged)", call("res.partner", "search_count",
                                             [[("category_id", "=", partner_tag), ("is_company", "=", False)]], NO_ARCHIVE)),
        ("maintenance.equipment", call("maintenance.equipment", "search_count", [[("category_id", "=", category_id)]], NO_ARCHIVE)),
        ("maintenance.request", call("maintenance.request", "search_count", [[("equipment_id", "in", list(eq_ids.values()))]])),
        ("project.task (requests)", call("project.task", "search_count", [[("project_id", "=", pid), ("parent_id", "=", False)]], NO_ARCHIVE)),
        ("project.task (samples)", call("project.task", "search_count", [[("project_id", "=", pid), ("parent_id", "!=", False)]], NO_ARCHIVE)),
    ]
    src = [len(data["clients"]), len(data["staff"]), len(data["equipment"]), n_maint, len(data["requests"]), n_samples]
    print(f"\n{'Odoo model':34} {'source':>6} {'odoo':>6}")
    for (label, n), s in zip(counts, src):
        print(f"{label:34} {s:6} {n:6}")
    base = URL
    print(f"\nOdoo URLs:\n  {base}/odoo/project\n  {base}/odoo/maintenance\n  {base}/odoo/contacts")


if __name__ == "__main__":
    main()
