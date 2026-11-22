"""Phase 2: the remaining Lab Tracker fields on Odoo 20. Re-runnable (search before create).
Ported for the labtracker-odoo image: connection from env (see odoo_api.py). The Supabase backfill and the
demo-data smoke test of the original script are not part of the image (see tools/load_demo_data.py).

Adds project.task fields x_scan / x_export / x_email / x_build_in (samples) and x_submitted / x_save_location (requests),
shows them with the add-on's numeric lab_time_taken on the task form + the Samples sub-task list, adds automation rules
"Lab sample: complete when all ticked" (all four toggles) and "Lab request: default submitted", adds the request
total time (lab_time_total) to the task list, and removes unused stock stages from project "Lab requests"
(only New / Active / Complete / Pending pickup stay).
The old free-text x_time_spent field is no longer created; on existing databases it is kept (data preserved), relabelled
"Time spent (old text)" and removed from the views. The add-on migration 20.0.2.2.0 copies its values to lab_time_taken.
Normally run by provision.py. Stand-alone:  ODOO_URL=... ODOO_DB=... ODOO_PASSWORD=... python3 lab_phase2.py
Never prints any secret.
"""
import os
import sys
import xmlrpc.client

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from odoo_api import call  # noqa: E402
from lab_rules import PROJECT_NAME, STAGES, ensure_project, ensure_rule, upsert, xmlid  # noqa: E402

# ---------------------------------------------------------------- 1+2. fields
FIELDS = [
    # name, type, label, extra
    ("x_scan", "boolean", "Scan", {}),
    ("x_export", "boolean", "Export", {}),
    ("x_email", "boolean", "Email", {}),
    ("x_build_in", "boolean", "Build in", {}),
    ("x_submitted", "date", "Submitted", {}),
    ("x_save_location", "char", "Data save location", {}),
]


def ensure_fields(task_model_id):
    existing = call("project.task", "fields_get", [[f[0] for f in FIELDS]], {"attributes": ["type"]})
    added = []
    for name, ttype, label, extra in FIELDS:
        if name in existing:
            continue
        call("ir.model.fields", "create", [{"model_id": task_model_id, "name": name, "ttype": ttype,
                                            "field_description": label, "state": "manual", **extra}])
        added.append(name)
    return added


# ---------------------------------------------------------------- views
VIEW_SAMPLE = """<data>
  <xpath expr="//field[@name='task_properties']" position="before">
    <group name="lab_sample" string="Sample checklist" invisible="not parent_id">
      <group>
        <field name="x_scan" widget="boolean_toggle"/>
        <field name="x_export" widget="boolean_toggle"/>
        <field name="x_email" widget="boolean_toggle"/>
        <field name="x_build_in" widget="boolean_toggle"/>
      </group>
      <group>
        <field name="lab_time_taken" widget="float_time" invisible="not parent_id"/>
      </group>
    </group>
  </xpath>
  <xpath expr="//page[@name='sub_tasks_page']//field[@name='child_ids']/list/field[@name='name']" position="after">
    <field name="x_scan" widget="boolean_toggle" optional="show"/>
    <field name="x_export" widget="boolean_toggle" optional="show"/>
    <field name="x_email" widget="boolean_toggle" optional="show"/>
    <field name="x_build_in" widget="boolean_toggle" optional="show"/>
    <field name="lab_time_taken" widget="float_time" optional="show" sum="Total"/>
  </xpath>
</data>"""

VIEW_REQUEST = """<data>
  <xpath expr="//div[@id='date_deadline_and_recurring_task']" position="after">
    <field name="x_submitted" invisible="parent_id"/>
    <field name="x_save_location" invisible="parent_id" placeholder="e.g. s3://lab/LAB-001"/>
  </xpath>
</data>"""


def ensure_views(form_view_id):
    out = {}
    for name, arch in (("Lab: sample fields on task", VIEW_SAMPLE), ("Lab: request fields on task", VIEW_REQUEST)):
        out[name] = upsert("ir.ui.view", [("name", "=", name), ("model", "=", "project.task")],
                           {"name": name, "model": "project.task", "inherit_id": form_view_id, "mode": "extension",
                            "type": "form", "priority": 99, "arch_db": arch, "active": True})
    return out


# ---------------------------------------------------------------- automation rules
CODE_COMPLETE = """for rec in records:
    if not rec.parent_id:
        continue
    proj = rec.project_id or rec.parent_id.project_id
    Stage = env['project.task.type']
    done = Stage.search([('name', '=', 'Complete'), ('project_ids', 'in', proj.ids)], limit=1)
    active = Stage.search([('name', '=', 'Active'), ('project_ids', 'in', proj.ids)], limit=1)
    all_ticked = bool(rec.x_scan and rec.x_export and rec.x_email and rec.x_build_in)
    if all_ticked and done:
        vals = {'state': '1_done'}
        if rec.stage_id != done:
            vals['stage_id'] = done.id
        rec.write(vals)
    elif not all_ticked and done and rec.stage_id == done and active:
        rec.write({'stage_id': active.id, 'state': '01_in_progress'})
"""

CODE_SUBMITTED = """for rec in records:
    if not rec.parent_id and not rec.x_submitted:
        rec.write({'x_submitted': datetime.date.today()})
"""


# ---------------------------------------------------------------- 2026-11-21: time taken (numeric) + request list column
OLD_TIME_LABEL = "Time spent (old text)"

VIEW_REQUEST_LIST = """<data>
  <xpath expr="//list" position="inside">
    <field name="lab_time_total" widget="float_time" optional="show" string="Time taken (h)"/>
  </xpath>
</data>"""


def retire_time_text():
    """Keep x_time_spent (if it exists) for its data, but label it as the old text so nobody types into it."""
    ids = call("ir.model.fields", "search_read", [[("model", "=", "project.task"), ("name", "=", "x_time_spent")]],
               {"fields": ["field_description"]})
    if not ids:
        return "absent"
    if ids[0]["field_description"] != OLD_TIME_LABEL:
        call("ir.model.fields", "write", [[ids[0]["id"]], {"field_description": OLD_TIME_LABEL}])
        return "relabelled"
    return "ok"


def ensure_request_list_view():
    """Request total time on the standard task list. Tolerant: a missing base view or xpath is reported, not fatal."""
    rec = call("ir.model.data", "search_read", [[("module", "=", "project"), ("name", "=", "view_task_tree2")]],
               {"fields": ["res_id"], "limit": 1})
    if not rec:
        return "skipped (project.view_task_tree2 not found)"
    try:
        return upsert("ir.ui.view", [("name", "=", "Lab: request time on task list"), ("model", "=", "project.task")],
                      {"name": "Lab: request time on task list", "model": "project.task", "inherit_id": rec[0]["res_id"],
                       "mode": "extension", "type": "list", "priority": 99, "arch_db": VIEW_REQUEST_LIST, "active": True})[1]
    except xmlrpc.client.Fault as e:
        return "FAILED: " + e.faultString.strip().splitlines()[-1][:120]


# ---------------------------------------------------------------- 3. stage cleanup
def clean_stages(pid):
    keep = set(STAGES)
    stages = call("project.task.type", "search_read", [[("project_ids", "in", [pid])]], {"fields": ["name", "project_ids"]})
    report = []
    for st in stages:
        if st["name"] in keep:
            continue
        n_here = call("project.task", "search_count", [[("project_id", "=", pid), ("stage_id", "=", st["id"])]],
                      {"context": {"active_test": False}})
        if n_here:
            report.append(f"kept '{st['name']}' (has {n_here} task(s) in project {pid})")
            continue
        others = [p for p in st["project_ids"] if p != pid]
        n_any = call("project.task", "search_count", [[("stage_id", "=", st["id"])]], {"context": {"active_test": False}})
        if others or n_any:
            call("project.task.type", "write", [[st["id"]], {"project_ids": [(3, pid)]}])
            report.append(f"unlinked '{st['name']}' (shared with {len(others)} other project(s))")
        else:
            call("project.task.type", "unlink", [[st["id"]]])
            report.append(f"deleted '{st['name']}' (only this project, 0 tasks)")
    return report or ["nothing to remove"]


def main():
    task_model_id = call("ir.model", "search", [[("model", "=", "project.task")]])[0]
    pid, _stages = ensure_project()
    form_view = xmlid("project", "view_task_form2")  # env.ref equivalent; never a hardcoded view id

    added = ensure_fields(task_model_id)
    print("fields added:", added or "none (all existed)")
    print("views:", ensure_views(form_view))
    print("old time text field:", retire_time_text())
    print("request list time column:", ensure_request_list_view())
    print("rule complete:", ensure_rule("Lab sample: complete when all ticked", task_model_id, "on_create_or_write", CODE_COMPLETE,
                                        "Complete sample when all ticked", ("x_scan", "x_export", "x_email", "x_build_in"),
                                        "[('parent_id','!=',False)]"))
    print("rule submitted:", ensure_rule("Lab request: default submitted", task_model_id, "on_create", CODE_SUBMITTED,
                                         "Default submitted to today", (), "[('parent_id','=',False)]"))
    print("stages:", clean_stages(pid))
    rules = call("base.automation", "search_read", [[("name", "ilike", "Lab")]], {"fields": ["name", "active"], "context": {"active_test": False}})
    print("rules:", [(r["name"], r["active"]) for r in rules])


if __name__ == "__main__":
    main()
