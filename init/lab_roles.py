"""Lab Tracker roles on Odoo 20: three groups, access rows, intern record rule, dashboard menu visibility,
optional test users, optional verification. Ported for the labtracker-odoo image (connection from env, see odoo_api.py).

Odoo 20 security model (differs from <= 17):
  * res.groups has privilege_id (res.groups.privilege) instead of category_id; implied_ids still exists.
  * ir.model.access and ir.rule are merged into ONE model `ir.access`:
      kind is COMPUTED from group_id: with a group -> 'permission' (grants operation on records in `domain`, OR-ed);
      without a group -> 'restriction' (global, AND-ed, denies outside `domain`). Restrictions cannot be group-scoped,
      but the domain is a safe_eval'd Python expression with `user`, so a global restriction can be
      "deny-all if user.has_group(<role>) else allow-all". That is how read-only / intern scoping is done here.
  * menus: ir.ui.menu.group_ids

Roles (privilege "Lab"):
  Lab / Manager      implies Project Admin, Maintenance Equipment Manager, Contact Creation, Sales(CRM) Admin
  Lab / Coordinator  implies Project User + Sales User: All Documents (for CRM visibility + menu),
                     plus permission 'r' on maintenance.request, plus global restrictions (operation cud) that deny
                     coordinators on maintenance.request / crm.lead.
                     Equipment: permission 'u' + restriction denying 'cd'; the lab_dashboard add-on limits the write
                     to x_condition, the Best practices / Maintenance procedures pages and the Contacts tab. Contacts (res.partner): permission 'cu' + restriction
                     denying 'd' (create, edit and archive allowed, no delete). Equipment contacts
                     (lab.equipment.contact): permission 'cu', no delete (read for all users and full access for
                     equipment managers come from the add-on's security/ir.access.csv). Equipment contact types
                     (lab.equipment.contact.type): permission 'cu' so coordinators can type a new one, no delete.
  Lab / Intern       implies only base internal user; permission 'u' on project.task scoped to "assigned to me OR parent
                     request assigned to me", plus a global restriction 'crud' on project.task with the same domain for
                     interns (allow-all for everyone else). The lab_dashboard add-on blocks assignee change by interns
                     (2026-11-22; the old automation rule is deleted by ensure_guard).
                     2026-11-21: permission 'c' on project.task limited to samples whose parent is a top-level Lab request
                     assigned to the intern (INTERN_SAMPLE_DOMAIN); no create of requests, no delete. The add-on also
                     rejects a new intern-created sample assigned to anyone other than the intern / the request's assignees.
                     Interns now see Lab Home (menus + server check), with numbers limited to their own work.
Test users manager@lab.test / coordinator@lab.test / intern@lab.test are created ONLY when LAB_CREATE_TEST_USERS=1.
Their passwords come from LAB_TEST_MANAGER_PW / LAB_TEST_COORDINATOR_PW / LAB_TEST_INTERN_PW; if one is unset a random
password is generated and NOT shown anywhere (reset it from Settings > Users). Nothing is ever written to a .env file.

Normally run by provision.py. Stand-alone:
  ODOO_URL=... ODOO_DB=... ODOO_PASSWORD=... [LAB_CREATE_TEST_USERS=1] python3 lab_roles.py [--verify]
--verify logs in as the three test users and checks their rights (needs the test users and demo request LAB-001).
Idempotent: every object is searched before it is created. Never prints any secret.
"""
import os
import secrets
import sys
import xmlrpc.client

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from odoo_api import DB, URL, call  # noqa: E402
from lab_rules import model_id, upsert, xmlid  # noqa: E402

XML_MODULE = "lab_tracker"
PRIVILEGE = "Lab"

INTERN_DOMAIN = "['|', ('user_ids', 'in', [user.id]), ('parent_id.user_ids', 'in', [user.id])]"
ALLOW_ALL, DENY_ALL = "[(1, '=', 1)]", "[(0, '=', 1)]"
# 2026-11-21: interns may add samples (sub-tasks) to top-level Lab requests assigned to them
INTERN_SAMPLE_DOMAIN = ("[('parent_id', '!=', False), ('parent_id.parent_id', '=', False), "
                        "('parent_id.project_id.name', '=', 'Lab requests'), ('parent_id.user_ids', 'in', [user.id])]")


def only_for(role_xml, restricted, otherwise=ALLOW_ALL):
    """Domain expression for a global restriction that only bites users of one Lab role."""
    return f"({restricted}) if user.has_group('{XML_MODULE}.{role_xml}') else ({otherwise})"

ROLES = {
    # name: (xml name, implied xml ids, comment)
    "Manager": ("group_lab_manager",
                [("project", "group_project_manager"), ("maintenance", "group_equipment_manager"),
                 ("base", "group_partner_manager"), ("sales_team", "group_sale_manager")],
                "Lab manager: full rights in Project, Maintenance, Contacts and CRM. No Settings / technical admin."),
    "Coordinator": ("group_lab_coordinator",
                    [("project", "group_project_user"), ("sales_team", "group_sale_salesman_all_leads")],
                    "Lab coordinator: creates and edits requests and samples; on equipment changes only condition, best practices, "
                    "maintenance procedures and contacts; creates, edits and archives "
                    "clients/contacts and equipment contacts (no delete); read-only on maintenance requests and CRM."),
    "Intern": ("group_lab_intern", [("base", "group_user")],
               "Lab intern: sees and edits only the requests/samples assigned to them (or whose request is assigned). No CRM."),
}

USERS = [  # login, name, role, env key holding the password (optional)
    ("manager@lab.test", "Test Manager", "Manager", "LAB_TEST_MANAGER_PW"),
    ("coordinator@lab.test", "Test Coordinator", "Coordinator", "LAB_TEST_COORDINATOR_PW"),
    ("intern@lab.test", "Test Intern", "Intern", "LAB_TEST_INTERN_PW"),
]
DASHBOARD_MENUS = ("menu_lab_root", "menu_lab_home_root", "menu_lab_home_project")  # xml ids in module lab_dashboard

# note: automation actions run with env.su=True but env.user is still the person who wrote, so test the user, not su


# ---------------------------------------------------------------- helpers
def ensure_xmlid(name, model, res_id):
    dom = [("module", "=", XML_MODULE), ("name", "=", name)]
    rec = call("ir.model.data", "search_read", [dom], {"fields": ["res_id"], "limit": 1})
    if rec:
        if rec[0]["res_id"] != res_id:
            call("ir.model.data", "write", [[rec[0]["id"]], {"res_id": res_id, "model": model}])
        return "exists"
    call("ir.model.data", "create", [{"module": XML_MODULE, "name": name, "model": model, "res_id": res_id, "noupdate": True}])
    return "created"


def ensure_privilege():
    # ir.module.category cannot be created over RPC (no access row), so the privilege sits under "Services" next to Project
    cat_id = call("ir.module.category", "search", [[("name", "=", "Services"), ("parent_id", "=", False)]], {"limit": 1})[0]
    pid, state = upsert("res.groups.privilege", [("name", "=", PRIVILEGE)],
                        {"name": PRIVILEGE, "category_id": cat_id, "sequence": 1,
                         "description": "Lab Tracker role: Manager, Coordinator or Intern"})
    return pid, state


def ensure_groups(priv_id):
    out = {}
    for name, (xname, implied, comment) in ROLES.items():
        gid, state = upsert("res.groups", [("name", "=", name), ("privilege_id", "=", priv_id)],
                            {"name": name, "privilege_id": priv_id, "comment": comment,
                             "implied_ids": [(6, 0, [xmlid(m, n) for m, n in implied])]})
        ensure_xmlid(xname, "res.groups", gid)
        out[name] = (gid, state)
    return out


def ensure_access(name, model, group_id, operation, domain=False):
    """group_id=False makes a global restriction (kind is computed by Odoo from group_id)."""
    mid = model_id(model)
    vals = {"name": name, "model_id": mid, "group_id": group_id, "operation": operation, "domain": domain, "active": True}
    return upsert("ir.access", [("name", "=", name), ("model_id", "=", mid)], vals)[1]


# rows from earlier versions that the current rules replace (removed on provisioning)
RETIRED_ACCESS = [
    ("Lab coordinator: read-only maintenance.equipment", "maintenance.equipment"),
    ("Lab coordinator: read-only contacts (own partner editable)", "res.partner"),
]


def retire_access():
    out = {}
    for name, model in RETIRED_ACCESS:
        ids = call("ir.access", "search", [[("name", "=", name), ("model_id", "=", model_id(model))]], {"context": {"active_test": False}})
        if ids:
            call("ir.access", "unlink", [ids])
        out[name] = "removed" if ids else "absent"
    return out


def ensure_access_rows(groups):
    coord, intern = groups["Coordinator"][0], groups["Intern"][0]
    res = {"retired": retire_access()}
    # Coordinator: see all maintenance requests (base user only sees own), deny any change on the read-only models
    res["coord read maintenance.request"] = ensure_access("Lab coordinator: read all maintenance requests", "maintenance.request", coord, "r")
    for model in ("maintenance.request", "crm.lead"):
        res[f"coord deny cud {model}"] = ensure_access(f"Lab coordinator: read-only {model}", model, False, "cud",
                                                       only_for("group_lab_coordinator", DENY_ALL))
    # Coordinator: equipment condition. The add-on (models/lab_equipment.py) rejects any field other than x_condition.
    res["coord update equipment"] = ensure_access("Lab coordinator: update equipment (condition only, enforced by lab_dashboard)",
                                                  "maintenance.equipment", coord, "u")
    res["coord deny cd equipment"] = ensure_access("Lab coordinator: no create/delete on maintenance.equipment", "maintenance.equipment",
                                                   False, "cd", only_for("group_lab_coordinator", DENY_ALL))
    # Coordinator: create, edit and archive clients and other contacts, never delete
    res["coord create/edit res.partner"] = ensure_access("Lab coordinator: create and edit contacts", "res.partner", coord, "cu")
    res["coord deny d res.partner"] = ensure_access("Lab coordinator: no delete on contacts", "res.partner", False, "d",
                                                    only_for("group_lab_coordinator", DENY_ALL))
    # Coordinator: equipment contact persons (model of the add-on); no unlink permission, so delete is denied
    res["coord create/edit lab.equipment.contact"] = ensure_access("Lab coordinator: create and edit equipment contacts",
                                                                   "lab.equipment.contact", coord, "cu")
    # ---- equipment features (lab_dashboard 20.0.2.3.0): free-text contact types; coordinators may add new ones, not delete
    res["coord create/edit lab.equipment.contact.type"] = ensure_access("Lab coordinator: create and edit equipment contact types",
                                                                        "lab.equipment.contact.type", coord, "cu")
    # ---- end equipment features
    # Intern: may update own tasks, may not see/touch anything else, may not create or delete (no c/d permission row)
    res["intern update own tasks"] = ensure_access("Lab intern: update assigned requests and samples", "project.task", intern, "u", INTERN_DOMAIN)
    # --- 2026-11-21: intern adds samples to assigned requests (create only; edit comes from the 'u' row above)
    res["intern create samples"] = ensure_access("Lab intern: add samples to assigned requests", "project.task", intern, "c",
                                                 INTERN_SAMPLE_DOMAIN)
    res["intern restrict tasks"] = ensure_access("Lab intern: only assigned requests and samples", "project.task", False, "crud",
                                                 only_for("group_lab_intern", INTERN_DOMAIN))
    return res


GUARD_RULE = "Lab intern: cannot change assignee"


def ensure_guard():
    """2026-11-22: the intern assignee guard lives in the lab_dashboard model (project.task.write), which raises a clean
    UserError. The old automation logged an ERROR on every denial, so it is deleted here (with its server action).
    Idempotent: 'deleted' the first time, 'absent' afterwards."""
    ids = call("base.automation", "search", [[("name", "=", GUARD_RULE)]], {"context": {"active_test": False}})
    if not ids:
        return "absent (guard is in lab_dashboard)"
    acts = call("ir.actions.server", "search", [[("base_automation_id", "in", ids)]], {"context": {"active_test": False}})
    call("base.automation", "unlink", [ids])
    acts = call("ir.actions.server", "search", [[("id", "in", acts)]], {"context": {"active_test": False}}) if acts else []
    if acts:
        call("ir.actions.server", "unlink", [acts])
    return "old automation deleted (guard is in lab_dashboard)"


# ---------------------------------------------------------------- test users (opt-in, passwords from env, never printed)
def env_get(key):
    return os.environ.get(key) or None


def ensure_users(groups):
    out = {}
    internal = xmlid("base", "group_user")
    for login, name, role, key in USERS:
        gid = groups[role][0]
        pw = env_get(key)
        vals = {"name": name, "login": login, "email": login, "group_ids": [(6, 0, [internal, gid])]}
        ids = call("res.users", "search", [[("login", "=", login)]], {"limit": 1, "context": {"active_test": False}})
        if ids:
            if pw:  # keep the password in sync with the env; otherwise leave whatever is set
                vals["password"] = pw
            call("res.users", "write", [ids, vals])
            out[login] = "updated"
        else:
            call("res.users", "create", [{**vals, "password": pw or secrets.token_urlsafe(18)}])
            out[login] = "created" + ("" if pw else " (random password, reset it in Settings > Users)")
    return out


def ensure_intern_assignment():
    uid = call("res.users", "search", [[("login", "=", "intern@lab.test")]], {"limit": 1})[0]
    task = call("project.task", "search_read", [[("name", "=like", "LAB-001%"), ("parent_id", "=", False)]], {"fields": ["user_ids"], "limit": 1})
    if not task:
        return "LAB-001 not found"
    if uid in task[0]["user_ids"]:
        return "already assigned"
    call("project.task", "write", [[task[0]["id"]], {"user_ids": [(4, uid)]}])
    return "assigned"


def ensure_dashboard_menus(groups):
    """Lab Home menus of the lab_dashboard add-on: visible to Lab Manager, Lab Coordinator, Lab Intern (own work only,
    2026-11-21) and Settings admins."""
    want = sorted([groups["Manager"][0], groups["Coordinator"][0], groups["Intern"][0], xmlid("base", "group_system")])
    out = {}
    for name in DASHBOARD_MENUS:
        rec = call("ir.model.data", "search_read", [[("module", "=", "lab_dashboard"), ("name", "=", name)]], {"fields": ["res_id"], "limit": 1})
        if not rec:
            out[name] = "not installed"
            continue
        mid = rec[0]["res_id"]
        if sorted(call("ir.ui.menu", "read", [[mid], ["group_ids"]])[0]["group_ids"]) != want:
            call("ir.ui.menu", "write", [[mid], {"group_ids": [(6, 0, want)]}])
            out[name] = "groups set"
        else:
            out[name] = "ok"
    return out


def ensure_menus(groups):
    """Settings is already admin-only (Access Rights / Administrator). Apps ships visible to every internal user in
    Odoo 20 (group 'Is regular user'), so pin it to Settings administrators. The Project root menu needs the Intern
    group added, otherwise interns cannot reach Project > Requests board / My Tasks (they are not Project users)."""
    out = {}
    apps = xmlid("base", "menu_management")
    sysg = xmlid("base", "group_system")
    if call("ir.ui.menu", "read", [[apps], ["group_ids"]])[0]["group_ids"] != [sysg]:
        call("ir.ui.menu", "write", [[apps], {"group_ids": [(6, 0, [sysg])]}])
        out["Apps"] = "pinned to Administrator"
    proj = xmlid("project", "menu_main_pm")
    if groups["Intern"][0] not in call("ir.ui.menu", "read", [[proj], ["group_ids"]])[0]["group_ids"]:
        call("ir.ui.menu", "write", [[proj], {"group_ids": [(4, groups["Intern"][0])]}])
        out["Project"] = "Intern group added"
    for m in call("ir.ui.menu", "search_read", [[("parent_id", "=", False), ("name", "in", ["Settings", "Apps", "Project"])]], {"fields": ["name", "group_ids"]}):
        out[m["name"] + " groups"] = [g["full_name"] for g in call("res.groups", "read", [m["group_ids"], ["full_name"]])]
    return out


# ---------------------------------------------------------------- verification as the test users
TEST_NAME = "Lab role test"


class Fail(Exception):
    pass


def as_user(login, key):
    pw = env_get(key)
    if not pw:
        raise Fail(f"{key} is not set in the environment")
    uid = xmlrpc.client.ServerProxy(f"{URL}/xmlrpc/2/common").authenticate(DB, login, pw, {})
    if not uid:
        raise Fail(f"login failed for {login}")
    proxy = xmlrpc.client.ServerProxy(f"{URL}/xmlrpc/2/object", allow_none=True)

    def run(model, method, args=None, kwargs=None):
        return proxy.execute_kw(DB, uid, pw, model, method, args or [], kwargs or {})
    return uid, run


def denied(fn, *a):
    try:
        fn(*a)
        return False
    except xmlrpc.client.Fault as e:
        msg = e.faultString
        return any(k in msg for k in ("AccessError", "not allowed", "doesn't have", "access to:", "top-secret", "cannot change"))


def root_menus(uid, run):
    """Top-bar apps for a user: root menus whose groups the user has, and whose action models they can read
    (ir.ui.menu.load_menus cannot be fetched over XML-RPC because its result contains None values).
    Apps needs read on ir.module.module and Settings is admin-group-only, so both drop out for the lab roles."""
    have = set(call("res.users", "read", [[uid], ["all_group_ids"]])[0]["all_group_ids"])
    out = []
    for m in call("ir.ui.menu", "search_read", [[("parent_id", "=", False)]], {"fields": ["name", "group_ids"]}):
        if m["group_ids"] and not have & set(m["group_ids"]):
            continue
        out.append(m["name"])
    return sorted(out)


def remove_test_records():
    """Delete every throwaway record the checks below create (all named / noted TEST_NAME)."""
    stray = call("project.task", "search", [[("name", "like", TEST_NAME)]], {"context": {"active_test": False}})
    if stray:
        call("project.task", "unlink", [stray])
    junk = call("lab.equipment.contact", "search", [["|", ("note", "=", TEST_NAME), ("partner_id.name", "=", TEST_NAME)]], {"context": {"active_test": False}})
    if junk:
        call("lab.equipment.contact", "unlink", [junk])
    junk = call("lab.equipment.contact.type", "search", [[("name", "=", TEST_NAME)]], {"context": {"active_test": False}})
    if junk:
        call("lab.equipment.contact.type", "unlink", [junk])
    for mdl in ("crm.lead", "res.partner", "maintenance.request", "maintenance.equipment"):
        junk = call(mdl, "search", [[("name", "=", TEST_NAME)]], {"context": {"active_test": False}})
        if junk:
            call(mdl, "unlink", [junk])


def verify():
    remove_test_records()  # leftovers of an earlier interrupted run
    results = {}
    eq = call("maintenance.equipment", "search_read", [[]], {"fields": ["name", "x_condition", "serial_no", "lab_best_practices", "lab_maintenance_procedures"], "limit": 1})[0]
    ctype = {n: call("ir.model.data", "search_read", [[("module", "=", "lab_dashboard"), ("name", "=", f"contact_type_{n}")]],
                     {"fields": ["res_id"], "limit": 1})[0]["res_id"] for n in ("sales", "support", "engineer")}
    partner = call("res.partner", "create", [{"name": TEST_NAME, "email": "lab-role-test@lab.test", "phone": "+1 555 0100"}])
    lab1 = call("project.task", "search_read", [[("name", "=like", "LAB-001%"), ("parent_id", "=", False)]], {"fields": ["child_ids", "project_id"], "limit": 1})[0]
    sample = call("project.task", "read", [[lab1["child_ids"][0]], ["x_scan", "user_ids"]])[0]
    other = call("project.task", "search", [[("parent_id", "=", False), ("id", "!=", lab1["id"]), ("project_id", "!=", False)]], {"limit": 1})[0]

    # Manager
    uid, m = as_user("manager@lab.test", "LAB_TEST_MANAGER_PW")
    m("maintenance.equipment", "write", [[eq["id"]], {"name": eq["name"] + " ~"}])
    m("maintenance.equipment", "write", [[eq["id"]], {"name": eq["name"]}])
    results["manager writes equipment"] = True
    results["manager create+delete crm.lead"] = bool(m("crm.lead", "unlink", [[m("crm.lead", "create", [{"name": "Lab role test"}])]]))
    ec = m("lab.equipment.contact", "create", [{"equipment_id": eq["id"], "partner_id": partner, "type_id": ctype["sales"], "note": TEST_NAME}])
    m("lab.equipment.contact", "write", [[ec], {"type_id": ctype["engineer"], "active": False}])
    results["manager create/write/archive/delete equipment contact"] = bool(m("lab.equipment.contact", "unlink", [[ec]]))
    results["manager menus"] = root_menus(uid, m)

    # Coordinator
    uid, c = as_user("coordinator@lab.test", "LAB_TEST_COORDINATOR_PW")
    results["coordinator reads equipment"] = len(c("maintenance.equipment", "search_read", [[]], {"fields": ["name"]})) > 0
    other_cond = "under_repair" if eq["x_condition"] != "under_repair" else "available"
    # Odoo 20 stores field tracking as mail.message rows of message_type 'tracking' (no mail.tracking.value model)
    tracking_dom = [("model", "=", "maintenance.equipment"), ("res_id", "=", eq["id"]), ("message_type", "=", "tracking")]
    tracked_before = call("mail.message", "search_count", [tracking_dom])
    c("maintenance.equipment", "write", [[eq["id"]], {"x_condition": other_cond}])
    c("maintenance.equipment", "write", [[eq["id"]], {"x_condition": eq["x_condition"]}])
    results["coordinator writes x_condition"] = call("maintenance.equipment", "read", [[eq["id"]], ["x_condition"]])[0]["x_condition"] == eq["x_condition"]
    results["x_condition change tracked in chatter"] = call("mail.message", "search_count", [tracking_dom]) >= tracked_before + 2
    results["coordinator write equipment name denied"] = denied(c, "maintenance.equipment", "write", [[eq["id"]], {"name": eq["name"] + " ~"}])
    results["coordinator write condition+name denied"] = denied(c, "maintenance.equipment", "write", [[eq["id"]], {"x_condition": other_cond, "name": eq["name"]}])
    # ---- equipment features (lab_dashboard 20.0.2.3.0): Best practices / Maintenance procedures editable by coordinators
    c("maintenance.equipment", "write", [[eq["id"]], {"lab_best_practices": f"<p>{TEST_NAME}</p>", "lab_maintenance_procedures": f"<p>{TEST_NAME}</p>"}])
    results["coordinator writes best practices + procedures"] = TEST_NAME in (call("maintenance.equipment", "read", [[eq["id"]], ["lab_best_practices"]])[0]["lab_best_practices"] or "")
    call("maintenance.equipment", "write", [[eq["id"]], {"lab_best_practices": eq["lab_best_practices"], "lab_maintenance_procedures": eq["lab_maintenance_procedures"]}])
    results["coordinator write serial_no denied"] = denied(c, "maintenance.equipment", "write", [[eq["id"]], {"serial_no": (eq["serial_no"] or "") + " ~"}])
    # ---- end equipment features
    results["coordinator archive equipment denied"] = denied(c, "maintenance.equipment", "write", [[eq["id"]], {"active": False}])
    results["coordinator create equipment denied"] = denied(c, "maintenance.equipment", "create", [{"name": TEST_NAME}])
    results["coordinator create maintenance.request denied"] = denied(c, "maintenance.request", "create", [{"name": "Lab role test", "equipment_id": eq["id"]}])
    results["coordinator reads crm.lead"] = isinstance(c("crm.lead", "search_read", [[]], {"fields": ["name"], "limit": 1}), list)
    results["coordinator create crm.lead denied"] = denied(c, "crm.lead", "create", [{"name": "Lab role test"}])
    results["coordinator write crm.lead denied"] = denied(c, "crm.lead", "write", [c("crm.lead", "search", [[]], {"limit": 1}), {"name": "x"}]) if c("crm.lead", "search", [[]], {"limit": 1}) else "no lead to test"
    cp = c("res.partner", "create", [{"name": TEST_NAME, "email": "lab-role-test-2@lab.test", "phone": "+1 555 0101"}])
    c("res.partner", "write", [[cp], {"comment": "Lab role test"}])
    results["coordinator create+edit res.partner"] = bool(cp)
    results["coordinator unlink res.partner denied"] = denied(c, "res.partner", "unlink", [[cp]])
    c("res.partner", "write", [[cp], {"active": False}])
    results["coordinator archive res.partner"] = call("res.partner", "read", [[cp], ["active"]])[0]["active"] is False
    cec = c("lab.equipment.contact", "create", [{"equipment_id": eq["id"], "partner_id": partner, "type_id": ctype["support"], "note": TEST_NAME}])
    # free-text type: the many2one quick-create calls name_create with whatever the user typed
    typed = c("lab.equipment.contact.type", "name_create", [TEST_NAME])[0]
    c("lab.equipment.contact", "write", [[cec], {"type_id": typed}])
    results["coordinator types a new contact type"] = call("lab.equipment.contact", "read", [[cec], ["type_id"]])[0]["type_id"][0] == typed
    results["coordinator unlink contact type denied"] = denied(c, "lab.equipment.contact.type", "unlink", [[typed]])
    results["coordinator create+edit equipment contact"] = bool(cec)
    results["coordinator unlink equipment contact denied"] = denied(c, "lab.equipment.contact", "unlink", [[cec]])
    c("lab.equipment.contact", "write", [[cec], {"active": False}])
    results["coordinator archive equipment contact"] = call("lab.equipment.contact", "read", [[cec], ["active"]])[0]["active"] is False
    c("lab.equipment.contact", "write", [[cec], {"active": True}])
    cec2 = c("maintenance.equipment", "write", [[eq["id"]], {"lab_contact_ids": [(0, 0, {"partner_id": partner, "type_id": ctype["sales"], "note": TEST_NAME})]}])
    results["coordinator adds contact from equipment Contacts tab"] = bool(cec2)
    results["coordinator edits own partner"] = bool(c("res.partner", "write", [[c("res.users", "read", [[uid], ["partner_id"]])[0]["partner_id"][0]], {"comment": False}]))
    results["coordinator reads res.partner"] = len(c("res.partner", "search_read", [[]], {"fields": ["name"], "limit": 1})) > 0
    c("project.task", "write", [[sample["id"]], {"x_scan": not sample["x_scan"]}])
    c("project.task", "write", [[sample["id"]], {"x_scan": sample["x_scan"]}])
    results["coordinator writes task"] = True
    results["coordinator sees all requests"] = other in c("project.task", "search", [[("parent_id", "=", False)]])
    results["coordinator menus"] = root_menus(uid, c)

    # Intern
    iuid, i = as_user("intern@lab.test", "LAB_TEST_INTERN_PW")
    seen = i("project.task", "search_read", [[]], {"fields": ["name", "parent_id"]})
    expected = {lab1["id"], *lab1["child_ids"]}
    results["intern sees only LAB-001 + samples"] = ({t["id"] for t in seen} == expected, [t["name"] for t in seen])
    results["intern read other request denied/empty"] = i("project.task", "search", [[("id", "=", other)]]) == []
    i("project.task", "write", [[sample["id"]], {"x_scan": not sample["x_scan"]}])
    i("project.task", "write", [[sample["id"]], {"x_scan": sample["x_scan"]}])
    results["intern writes sample x_scan"] = True
    change = [(6, 0, [])] if sample["user_ids"] else [(4, uid)]  # must differ from the current value or nothing is written
    results["intern change user_ids denied"] = denied(i, "project.task", "write", [[sample["id"]], {"user_ids": change}])
    tmp = call("project.task", "create", [{"name": "Lab role test sample", "parent_id": lab1["id"], "project_id": lab1["project_id"][0]}])
    results["intern unlink denied"] = denied(i, "project.task", "unlink", [[tmp]])
    # 2026-11-21: interns add samples to their own requests, nothing else
    own_sample = i("project.task", "create", [{"name": "Lab role test sample 2", "parent_id": lab1["id"], "project_id": lab1["project_id"][0]}])
    results["intern creates sample under assigned request"] = bool(own_sample)
    results["intern edits own new sample"] = bool(i("project.task", "write", [[own_sample], {"x_scan": True}]))
    results["intern create top-level request denied"] = denied(i, "project.task", "create", [{"name": "Lab role test request", "project_id": lab1["project_id"][0], "user_ids": [(6, 0, [iuid])]}])
    results["intern create sample under other request denied"] = denied(i, "project.task", "create", [{"name": "Lab role test sample 3", "parent_id": other, "project_id": lab1["project_id"][0]}])
    dash = i("lab.dashboard", "get_data", [])
    results["intern opens Lab Home (own work only)"] = dash.get("scope") == "assigned"
    results["intern reads equipment"] = len(i("maintenance.equipment", "search_read", [[]], {"fields": ["name"], "limit": 1})) > 0
    results["intern reads res.partner"] = len(i("res.partner", "search_read", [[]], {"fields": ["name"], "limit": 1})) > 0
    results["intern reads equipment contacts"] = len(i("lab.equipment.contact", "search_read", [[("id", "=", cec)]], {"fields": ["partner_id", "type_id", "phone", "email"]})) == 1
    results["intern create equipment contact denied"] = denied(i, "lab.equipment.contact", "create", [{"equipment_id": eq["id"], "partner_id": partner}])
    results["intern crm.lead denied"] = denied(i, "crm.lead", "search_read", [[]], {"fields": ["name"]})
    results["intern write best practices denied"] = denied(i, "maintenance.equipment", "write", [[eq["id"]], {"lab_best_practices": "<p>x</p>"}])
    results["intern create contact type denied"] = denied(i, "lab.equipment.contact.type", "create", [{"name": TEST_NAME}])
    results["intern menus"] = root_menus(iuid, i)

    # make sure the test data is back to what it was (delete anything a test may have created)
    call("project.task", "write", [[sample["id"]], {"x_scan": sample["x_scan"], "user_ids": [(6, 0, sample["user_ids"])]}])
    remove_test_records()
    after = call("project.task", "read", [[sample["id"]], ["x_scan", "user_ids"]])[0]
    results["test data reverted"] = after["x_scan"] == sample["x_scan"] and after["user_ids"] == sample["user_ids"] and \
        call("maintenance.equipment", "read", [[eq["id"]], ["name", "x_condition"]])[0] == {"id": eq["id"], "name": eq["name"], "x_condition": eq["x_condition"]} and \
        not call("res.partner", "search", [[("name", "=", TEST_NAME)]], {"context": {"active_test": False}})
    return results


# ---------------------------------------------------------------- main
def main(do_verify=False):
    priv_id, pstate = ensure_privilege()
    print("privilege:", PRIVILEGE, pstate)
    groups = ensure_groups(priv_id)
    print("groups:", groups)
    print("access rows:", ensure_access_rows(groups))
    print("intern assignee guard:", ensure_guard())
    if os.environ.get("LAB_CREATE_TEST_USERS") == "1":
        print("test users:", ensure_users(groups))
        print("intern on LAB-001:", ensure_intern_assignment())
    else:
        print("test users: skipped (set LAB_CREATE_TEST_USERS=1 to create manager@/coordinator@/intern@lab.test)")
    print("menus:", ensure_menus(groups))
    print("dashboard menus:", ensure_dashboard_menus(groups))
    if do_verify:
        print("verify:")
        for k, v in verify().items():
            print(f"  {k}: {v}")
    return groups


if __name__ == "__main__":
    main(do_verify="--verify" in sys.argv)
