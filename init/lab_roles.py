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
                     coordinators on maintenance.equipment / maintenance.request / crm.lead, and on res.partner except own partner
  Lab / Intern       implies only base internal user; permission 'u' on project.task scoped to "assigned to me OR parent
                     request assigned to me", plus a global restriction 'crud' on project.task with the same domain for
                     interns (allow-all for everyone else). Automation blocks assignee change by interns.
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
from lab_rules import ensure_rule, model_id, upsert, xmlid  # noqa: E402

XML_MODULE = "lab_tracker"
PRIVILEGE = "Lab"

INTERN_DOMAIN = "['|', ('user_ids', 'in', [user.id]), ('parent_id.user_ids', 'in', [user.id])]"
ALLOW_ALL, DENY_ALL = "[(1, '=', 1)]", "[(0, '=', 1)]"
OWN_PARTNER = "[('id', '=', user.partner_id.id)]"


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
                    "Lab coordinator: creates and edits requests and samples; read-only on equipment, maintenance requests, contacts and CRM."),
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
CODE_GUARD = """grp = env.ref('lab_tracker.group_lab_intern', raise_if_not_found=False)
if grp and env.user in grp.all_user_ids:
    raise UserError("Interns cannot change who a request or sample is assigned to.")
"""


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


def ensure_access_rows(groups):
    coord, intern = groups["Coordinator"][0], groups["Intern"][0]
    res = {}
    # Coordinator: see all maintenance requests (base user only sees own), deny any change on the read-only models
    res["coord read maintenance.request"] = ensure_access("Lab coordinator: read all maintenance requests", "maintenance.request", coord, "r")
    for model in ("maintenance.equipment", "maintenance.request", "crm.lead"):
        res[f"coord deny cud {model}"] = ensure_access(f"Lab coordinator: read-only {model}", model, False, "cud",
                                                       only_for("group_lab_coordinator", DENY_ALL))
    res["coord deny cud res.partner (own ok)"] = ensure_access("Lab coordinator: read-only contacts (own partner editable)", "res.partner", False, "cud",
                                                               only_for("group_lab_coordinator", OWN_PARTNER))
    # Intern: may update own tasks, may not see/touch anything else, may not create or delete (no c/d permission row)
    res["intern update own tasks"] = ensure_access("Lab intern: update assigned requests and samples", "project.task", intern, "u", INTERN_DOMAIN)
    res["intern restrict tasks"] = ensure_access("Lab intern: only assigned requests and samples", "project.task", False, "crud",
                                                 only_for("group_lab_intern", INTERN_DOMAIN))
    return res


def ensure_guard():
    return ensure_rule("Lab intern: cannot change assignee", model_id("project.task"), "on_write", CODE_GUARD,
                       "Block assignee change by interns", ("user_ids",))


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
    """Lab Home menus of the lab_dashboard add-on: visible to Lab Manager, Lab Coordinator and Settings admins only."""
    want = sorted([groups["Manager"][0], groups["Coordinator"][0], xmlid("base", "group_system")])
    out = {}
    for name in DASHBOARD_MENUS:
        rec = call("ir.model.data", "search_read", [[("module", "=", "lab_dashboard"), ("name", "=", name)]], {"fields": ["res_id"], "limit": 1})
        if not rec:
            out[name] = "not installed"
            continue
        mid = rec[0]["res_id"]
        if sorted(call("ir.ui.menu", "read", [[mid], ["group_ids"]])[0]["group_ids"]) != want:
            call("ir.ui.menu", "write", [[mid], {"group_ids": [(6, 0, want)]}])
            out[name] = "restricted"
        else:
            out[name] = "ok"
    return out


def ensure_menus(groups):
    """Settings is already admin-only (Access Rights / Administrator). Apps ships visible to every internal user in
    Odoo 20 (group 'Is regular user'), so pin it to Settings administrators. The Project root menu needs the Intern
    group added, otherwise interns cannot reach Project > Lab home / My Tasks (they are not Project users)."""
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


def verify():
    results = {}
    eq = call("maintenance.equipment", "search_read", [[]], {"fields": ["name"], "limit": 1})[0]
    lab1 = call("project.task", "search_read", [[("name", "=like", "LAB-001%"), ("parent_id", "=", False)]], {"fields": ["child_ids", "project_id"], "limit": 1})[0]
    sample = call("project.task", "read", [[lab1["child_ids"][0]], ["x_scan", "user_ids"]])[0]
    other = call("project.task", "search", [[("parent_id", "=", False), ("id", "!=", lab1["id"]), ("project_id", "!=", False)]], {"limit": 1})[0]

    # Manager
    uid, m = as_user("manager@lab.test", "LAB_TEST_MANAGER_PW")
    m("maintenance.equipment", "write", [[eq["id"]], {"name": eq["name"] + " ~"}])
    m("maintenance.equipment", "write", [[eq["id"]], {"name": eq["name"]}])
    results["manager writes equipment"] = True
    results["manager create+delete crm.lead"] = bool(m("crm.lead", "unlink", [[m("crm.lead", "create", [{"name": "Lab role test"}])]]))
    results["manager menus"] = root_menus(uid, m)

    # Coordinator
    uid, c = as_user("coordinator@lab.test", "LAB_TEST_COORDINATOR_PW")
    results["coordinator reads equipment"] = len(c("maintenance.equipment", "search_read", [[]], {"fields": ["name"]})) > 0
    results["coordinator write equipment denied"] = denied(c, "maintenance.equipment", "write", [[eq["id"]], {"name": eq["name"]}])
    results["coordinator create maintenance.request denied"] = denied(c, "maintenance.request", "create", [{"name": "Lab role test", "equipment_id": eq["id"]}])
    results["coordinator reads crm.lead"] = isinstance(c("crm.lead", "search_read", [[]], {"fields": ["name"], "limit": 1}), list)
    results["coordinator create crm.lead denied"] = denied(c, "crm.lead", "create", [{"name": "Lab role test"}])
    results["coordinator write crm.lead denied"] = denied(c, "crm.lead", "write", [c("crm.lead", "search", [[]], {"limit": 1}), {"name": "x"}]) if c("crm.lead", "search", [[]], {"limit": 1}) else "no lead to test"
    results["coordinator create res.partner denied"] = denied(c, "res.partner", "create", [{"name": "Lab role test"}])
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
    results["intern create denied"] = denied(i, "project.task", "create", [{"name": "Lab role test sample 2", "parent_id": lab1["id"], "project_id": lab1["project_id"][0]}])
    results["intern reads equipment"] = len(i("maintenance.equipment", "search_read", [[]], {"fields": ["name"], "limit": 1})) > 0
    results["intern reads res.partner"] = len(i("res.partner", "search_read", [[]], {"fields": ["name"], "limit": 1})) > 0
    results["intern crm.lead denied"] = denied(i, "crm.lead", "search_read", [[]], {"fields": ["name"]})
    results["intern menus"] = root_menus(iuid, i)

    # make sure the test data is back to what it was (delete anything a test may have created)
    call("project.task", "write", [[sample["id"]], {"x_scan": sample["x_scan"], "user_ids": [(6, 0, sample["user_ids"])]}])
    stray = call("project.task", "search", [[("name", "like", "Lab role test")]], {"context": {"active_test": False}})
    if stray:
        call("project.task", "unlink", [stray])
    for mdl in ("crm.lead", "res.partner", "maintenance.request"):
        junk = call(mdl, "search", [[("name", "=", "Lab role test")]], {"context": {"active_test": False}})
        if junk:
            call(mdl, "unlink", [junk])
    after = call("project.task", "read", [[sample["id"]], ["x_scan", "user_ids"]])[0]
    results["test data reverted"] = after["x_scan"] == sample["x_scan"] and after["user_ids"] == sample["user_ids"] and \
        call("maintenance.equipment", "read", [[eq["id"]], ["name"]])[0]["name"] == eq["name"]
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
