"""Lab Tracker: the hand-made Odoo 20 customisations as a re-runnable script (search before create, no hardcoded ids).
Ported for the labtracker-odoo image: connection from env (see odoo_api.py), creates project "Lab requests" if missing.

Creates / updates:
  1. ir.sequence 'lab.request' (LAB-, padding 3, no_gap) + automation "Lab request: add LAB- code"
  2. Field labels child_ids / subtask_count -> "Samples", parent_id -> "Request" + view "Lab: call sub-tasks Samples"
  3. project.task.x_equipment_id (many2one maintenance.equipment) + view "Lab: equipment on task"
     + automations "Lab sample: inherit equipment" and "Lab: block equipment that is down"
  4. maintenance.equipment.x_condition selection + badge on list/kanban/form + search filters
  5. Saved filters on project.task + window action "Lab home" + first menu item under Project
Every xml id is resolved through ir.model.data (env.ref equivalent); the project is found by name.
Normally run by provision.py inside the container. Stand-alone:
  ODOO_URL=http://host:8069 ODOO_DB=labtracker ODOO_PASSWORD=... python3 lab_rules.py [--trim-menus]
Never prints any secret.
"""
import os
import sys
import xmlrpc.client

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from odoo_api import call  # noqa: E402

PROJECT_NAME = "Lab requests"
STAGES = ["New", "Active", "Blocked", "Complete"]
EQUIPMENT_CATEGORY = "Lab instruments"


def xmlid(module, name):
    rec = call("ir.model.data", "search_read", [[("module", "=", module), ("name", "=", name)]], {"fields": ["res_id"], "limit": 1})
    if not rec:
        raise SystemExit(f"xml id {module}.{name} not found")
    return rec[0]["res_id"]


def upsert(model, domain, vals):
    ids = call(model, "search", [domain], {"limit": 1, "context": {"active_test": False}})
    if ids:
        call(model, "write", [ids, vals])
        return ids[0], "updated"
    return call(model, "create", [vals]), "created"


def model_id(model):
    return call("ir.model", "search", [[("model", "=", model)]], {"limit": 1})[0]


def ensure_view(name, model, inherit_id, vtype, arch):
    return upsert("ir.ui.view", [("name", "=", name), ("model", "=", model)],
                  {"name": name, "model": model, "inherit_id": inherit_id, "mode": "extension", "type": vtype,
                   "priority": 99, "arch_db": arch, "active": True})


def ensure_rule(name, mid, trigger, code, action_name, trigger_fields=(), filter_domain=False, model="project.task"):
    field_ids = call("ir.model.fields", "search", [[("model", "=", model), ("name", "in", list(trigger_fields))]]) if trigger_fields else []
    vals = {"name": name, "model_id": mid, "trigger": trigger, "active": True,
            "filter_domain": filter_domain, "trigger_field_ids": [(6, 0, field_ids)]}
    ids = call("base.automation", "search", [[("name", "=", name)]], {"limit": 1, "context": {"active_test": False}})
    act_vals = {"name": action_name, "state": "code", "code": code, "model_id": mid, "usage": "base_automation"}
    if ids:
        call("base.automation", "write", [ids, vals])
        act = call("ir.actions.server", "search", [[("base_automation_id", "=", ids[0])]], {"limit": 1})
        if act:
            call("ir.actions.server", "write", [act, act_vals])
        else:
            call("ir.actions.server", "create", [{**act_vals, "base_automation_id": ids[0]}])
        return ids[0], "updated"
    return call("base.automation", "create", [{**vals, "action_server_ids": [(0, 0, act_vals)]}]), "created"


# ---------------------------------------------------------------- 1. sequence + LAB- code
CODE_LAB = """for rec in records:
    if rec.parent_id or (rec.name or '').startswith('LAB-'):
        continue
    rec.write({'name': env['ir.sequence'].next_by_code('lab.request') + ' · ' + (rec.name or '')})
"""


def ensure_sequence(pid):
    """Start numbering after the highest LAB-nnn already loaded so re-imports never collide."""
    import re
    names = call("project.task", "search_read", [[("project_id", "=", pid), ("name", "=like", "LAB-%")]],
                 {"fields": ["name"], "context": {"active_test": False}})
    nums = [int(m.group(1)) for t in names if (m := re.match(r"LAB-(\d+)", t["name"] or ""))]
    vals = {"name": "Lab request code", "code": "lab.request", "prefix": "LAB-", "padding": 3,
            "implementation": "no_gap", "number_increment": 1}
    ids = call("ir.sequence", "search", [[("code", "=", "lab.request")]], {"limit": 1})
    if ids:
        cur = call("ir.sequence", "read", [ids, ["number_next_actual"]])[0]["number_next_actual"]
        if nums and cur <= max(nums):
            vals["number_next_actual"] = max(nums) + 1
        call("ir.sequence", "write", [ids, vals])
        return ids[0], "updated"
    vals["number_next_actual"] = (max(nums) + 1) if nums else 1
    return call("ir.sequence", "create", [vals]), "created"


# ---------------------------------------------------------------- 2. Samples wording
LABELS = {"child_ids": "Samples", "subtask_count": "Samples", "parent_id": "Request"}

VIEW_SAMPLES = """<data>
  <xpath expr="//page[@name='sub_tasks_page']" position="attributes">
    <attribute name="string">Samples</attribute>
  </xpath>
  <xpath expr="//button[@name='action_open_subtasks']//span[@class='o_stat_text']" position="replace">
    <span class="o_stat_text">Samples</span>
  </xpath>
  <!-- view-level labels survive registry reloads; the ir.model.fields rename below does not (Odoo re-reflects base fields) -->
  <xpath expr="//page[@name='sub_tasks_page']//field[@name='child_ids']" position="attributes">
    <attribute name="string">Samples</attribute>
  </xpath>
  <xpath expr="//field[@name='parent_id'][@invisible='not project_id and not is_template']" position="attributes">
    <attribute name="string">Request</attribute>
  </xpath>
</data>"""


def ensure_labels():
    """Note: Odoo rewrites base-field labels from code on every registry reload (new manual field, module update),
    so run this script last or re-run it; the view attributes in VIEW_SAMPLES hold regardless."""
    out = {}
    for f in call("ir.model.fields", "search_read", [[("model", "=", "project.task"), ("name", "in", list(LABELS))]],
                  {"fields": ["name", "field_description"]}):
        want = LABELS[f["name"]]
        if f["field_description"] != want:
            try:
                call("ir.model.fields", "write", [[f["id"]], {"field_description": want}])
                out[f["name"]] = want
            except xmlrpc.client.Fault as e:
                out[f["name"]] = "FAILED: " + e.faultString.strip().splitlines()[-1][:100]
        else:
            out[f["name"]] = "ok"
    return out


# ---------------------------------------------------------------- 3. equipment on tasks
VIEW_EQUIP = """<data>
  <xpath expr="//field[@name='partner_id']" position="after">
    <field name="x_equipment_id" options="{'no_create': True}" domain="[('x_condition','=','available')]"/>
  </xpath>
</data>"""

CODE_INHERIT = """for rec in records:
    if rec.parent_id and not rec.x_equipment_id and rec.parent_id.x_equipment_id:
        rec.write({'x_equipment_id': rec.parent_id.x_equipment_id.id})
"""

CODE_BLOCK = """for rec in records:
    eq = rec.x_equipment_id
    if eq and eq.x_condition and eq.x_condition != 'available':
        raise UserError("%s is %s and cannot be put on a request or sample. Pick another instrument or mark it Available first." % (eq.name, dict(eq._fields['x_condition'].selection).get(eq.x_condition, eq.x_condition)))
"""


def ensure_field(model, mid, name, vals):
    if name in call(model, "fields_get", [[name]], {"attributes": ["type"]}):
        return "exists"
    call("ir.model.fields", "create", [{"model_id": mid, "name": name, "state": "manual", **vals}])
    return "created"


# ---------------------------------------------------------------- 4. equipment condition
CONDITIONS = [("available", "Available"), ("under_repair", "Under repair"), ("out_of_service", "Out of service")]
BADGE = ('<field name="x_condition" widget="badge" decoration-success="x_condition == \'available\'" '
         'decoration-warning="x_condition == \'under_repair\'" decoration-danger="x_condition == \'out_of_service\'"/>')
EQ_VIEWS = [
    ("maintenance.equipment.form.lab_condition", "form", "hr_equipment_view_form",
     '<xpath expr="(//field[@name=\'category_id\'])[1]" position="after"><field name="x_condition" widget="selection_badge"/></xpath>'),
    ("maintenance.equipment.kanban.lab_condition", "kanban", "hr_equipment_view_kanban",
     f'<xpath expr="//field[@name=\'serial_no\']" position="after">{BADGE}</xpath>'),
    ("maintenance.equipment.list.lab_condition", "list", "hr_equipment_view_tree",
     f'<xpath expr="//field[@name=\'serial_no\']" position="after">{BADGE}</xpath>'),
    ("maintenance.equipment.search.lab_condition", "search", "hr_equipment_view_search",
     '<xpath expr="//filter[@name=\'under_maintenance\']" position="after">'
     '<filter string="Equipment down" name="equipment_down" domain="[(\'x_condition\', \'!=\', \'available\')]"/>'
     '<filter string="Lab instruments" name="lab_instruments" domain="[(\'category_id.name\', \'=\', \'Lab instruments\')]"/></xpath>'),
]


def ensure_condition(eq_mid):
    res = ensure_field("maintenance.equipment", eq_mid, "x_condition",
                       {"ttype": "selection", "field_description": "Condition", "copied": True,
                        "selection_ids": [(0, 0, {"value": v, "name": n, "sequence": i}) for i, (v, n) in enumerate(CONDITIONS)]})
    if res == "exists":  # make sure every option is there
        fid = call("ir.model.fields", "search", [[("model", "=", "maintenance.equipment"), ("name", "=", "x_condition")]])[0]
        have = {s["value"] for s in call("ir.model.fields.selection", "search_read", [[("field_id", "=", fid)]], {"fields": ["value"]})}
        for i, (v, n) in enumerate(CONDITIONS):
            if v not in have:
                call("ir.model.fields.selection", "create", [{"field_id": fid, "value": v, "name": n, "sequence": i}])
    views = {}
    for name, vtype, xml, arch in EQ_VIEWS:
        views[name] = ensure_view(name, "maintenance.equipment", xmlid("maintenance", xml), vtype, arch)[1]
    return res, views


# ---------------------------------------------------------------- 5. filters, Lab home action + menu
def filters_for(pid):
    base = [("project_id", "=", pid), ("parent_id", "=", False)]
    open_ = base + [("stage_id.name", "!=", "Complete")]
    return [
        ("Open requests", open_, False),
        ("Unassigned", open_ + [("user_ids", "=", False)], False),
        ("Overdue", open_ + [("date_deadline", "<", "today")], False),
        ("Blocked", base + [("stage_id.name", "=", "Blocked")], False),
        ("Due in 14 days", open_ + [("date_deadline", ">=", "today"), ("date_deadline", "<=", "today +14d")], False),
        ("Needs attention", open_ + ["|", "|", ("date_deadline", "<", "today"), ("stage_id.name", "=", "Blocked"), ("user_ids", "=", False)], True),
    ]


def ensure_lab_home(pid):
    act_vals = {"name": "Lab home", "res_model": "project.task", "view_mode": "kanban,list,form,calendar", "target": "current",
                "domain": repr([("project_id", "=", pid), ("parent_id", "=", False)]),
                "context": repr({"default_project_id": pid, "search_default_needs_attention": 1}),
                "help": '<p class="o_view_nocontent_smiling_face">Lab requests only (no samples). Use the favourites filters: '
                        'Open, Unassigned, Overdue, Blocked, Due in 14 days, Needs attention.</p>'}
    aid, a_state = upsert("ir.actions.act_window", [("name", "=", "Lab home"), ("res_model", "=", "project.task")], act_vals)
    out = {}
    for name, dom, default in filters_for(pid):
        out[name] = upsert("ir.filters", [("name", "=", name), ("model_id", "=", "project.task"), ("action_id", "=", aid)],
                           {"name": name, "model_id": "project.task", "domain": repr(dom), "context": "{}", "sort": "[]",
                            "user_ids": [(6, 0, [])], "action_id": aid, "is_default": default, "active": True})[1]
    menu = upsert("ir.ui.menu", [("name", "=", "Lab home"), ("parent_id", "=", xmlid("project", "menu_main_pm"))],
                  {"name": "Lab home", "parent_id": xmlid("project", "menu_main_pm"), "sequence": 0,
                   "action": f"ir.actions.act_window,{aid}"})
    return a_state, out, menu[1]


# ---------------------------------------------------------------- 0. project + stages (same shape as the demo loader)
def ensure_project():
    ids = call("project.project", "search", [[("name", "=", PROJECT_NAME)]], {"limit": 1, "context": {"active_test": False}})
    pid = ids[0] if ids else call("project.project", "create", [{"name": PROJECT_NAME}])
    stages = {}
    for seq, name in enumerate(STAGES, start=1):
        sids = call("project.task.type", "search", [[("name", "=", name), ("project_ids", "in", [pid])]], {"limit": 1})
        stages[name] = sids[0] if sids else call("project.task.type", "create",
                                                 [{"name": name, "sequence": seq, "fold": name == "Complete", "project_ids": [(4, pid)]}])
    return pid, stages


def ensure_equipment_category():
    ids = call("maintenance.equipment.category", "search", [[("name", "=", EQUIPMENT_CATEGORY)]], {"limit": 1})
    return ids[0] if ids else call("maintenance.equipment.category", "create", [{"name": EQUIPMENT_CATEGORY}])


# ---------------------------------------------------------------- main
def main():
    task_mid, eq_mid = model_id("project.task"), model_id("maintenance.equipment")
    pid, stages = ensure_project()
    print("project:", PROJECT_NAME, pid, "stages:", stages)
    print("equipment category:", ensure_equipment_category())
    form_view = xmlid("project", "view_task_form2")

    # 1
    print("sequence:", ensure_sequence(pid))
    print("rule LAB- code:", ensure_rule("Lab request: add LAB- code", task_mid, "on_create", CODE_LAB, "Add LAB- code", (),
                                         "[('project_id.name','=','Lab requests'),('parent_id','=',False)]"))
    # 2
    print("labels:", ensure_labels())
    print("view samples wording:", ensure_view("Lab: call sub-tasks Samples", "project.task", form_view, "form", VIEW_SAMPLES))
    # 4 (before 3: the task view domain and the block rule read x_condition)
    print("x_condition / equipment views:", ensure_condition(eq_mid))
    # 3
    print("x_equipment_id:", ensure_field("project.task", task_mid, "x_equipment_id",
                                          {"ttype": "many2one", "relation": "maintenance.equipment", "field_description": "Equipment",
                                           "tracking": 1, "copied": True, "on_delete": "set null"}))
    print("view equipment on task:", ensure_view("Lab: equipment on task", "project.task", form_view, "form", VIEW_EQUIP))
    print("rule inherit equipment:", ensure_rule("Lab sample: inherit equipment", task_mid, "on_create", CODE_INHERIT, "Inherit equipment",
                                                 (), "[('parent_id','!=',False)]"))
    print("rule block down equipment:", ensure_rule("Lab: block equipment that is down", task_mid, "on_create_or_write", CODE_BLOCK,
                                                    "Block down equipment", ("x_equipment_id",)))
    # 5
    print("Lab home (action, filters, menu):", ensure_lab_home(pid))

    rules = call("base.automation", "search_read", [[("name", "ilike", "Lab")]], {"fields": ["name", "active"], "context": {"active_test": False}})
    print("rules now:", [(r["name"], r["active"]) for r in rules])


# --- trim the top menu: hide apps the lab does not use (mail/calendar cannot be uninstalled; they are dependencies) ---
def trim_menus():
    hide = ['Discuss', 'Calendar', 'Link Tracker', 'Tests']
    ids = call('ir.ui.menu', 'search', [[['parent_id', '=', False], ['name', 'in', hide], ['active', '=', True]]])
    if ids:
        call('ir.ui.menu', 'write', [ids, {'active': False}])
    todo = call('ir.module.module', 'search_read', [[['name', '=', 'project_todo'], ['state', '=', 'installed']]], {'fields': ['id']})
    if todo:
        call('ir.module.module', 'button_immediate_uninstall', [[todo[0]['id']]])
    return {"hidden": len(ids), "project_todo_uninstalled": bool(todo)}


if __name__ == "__main__":
    main()
    if "--trim-menus" in sys.argv:
        print("trim menus:", trim_menus())
