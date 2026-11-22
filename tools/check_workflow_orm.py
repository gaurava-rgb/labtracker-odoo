"""Integration checks for odoo shell; every change is rolled back.

Covers the request workflow (New -> Active -> Complete -> Pending pickup), equipment-derived blocking, equipment that
stays on a task when it breaks, the equipment dropdown label, the Build in toggle, numeric time and its request
roll-up, and the intern rules (add samples to own requests, Lab Home limited to own work).
"""
from odoo.exceptions import UserError
from odoo.addons.lab_dashboard.models.lab_workflow import parse_hours


def check_workflow(env):
    admin = env.ref("base.user_admin")
    task_model = env["project.task"].with_user(admin).sudo(False).with_context(
        tracking_disable=True, mail_create_nosubscribe=True, mail_notrack=True,
    )
    project = task_model._lab_project()
    stages = task_model._lab_stages()
    passed = []

    def check(label, condition):
        if not condition:
            raise AssertionError(label)
        passed.append(label)
        print("PASS", label)

    def rejects(label, operation):
        class UnexpectedSuccess(Exception):
            pass
        try:
            with env.cr.savepoint():
                operation()
                raise UnexpectedSuccess(label)
        except UserError:
            passed.append(label)
            print("PASS", label)

    def request(name="Workflow check"):
        return task_model.create({"name": name, "project_id": project.id})

    try:
        actual = env["project.task.type"].search([("project_ids", "in", project.ids)], order="sequence,id").mapped("name")
        check("exact stage order New/Active/Complete/Pending pickup", actual == ["New", "Active", "Complete", "Pending pickup"])
        record = request()
        check("new requests start New", record.stage_id == stages["New"])
        rejects("cannot create directly Complete", lambda: task_model.create({
            "name": "invalid", "project_id": project.id, "stage_id": stages["Complete"].id,
        }))
        rejects("context defaults cannot bypass initial stage", lambda: task_model.with_context(
            default_project_id=project.id, default_stage_id=stages["Complete"].id,
        ).create({"name": "invalid context defaults"}))
        rejects("cannot skip New to Complete", lambda: record.write({"stage_id": stages["Complete"].id}))
        rejects("Done status cannot bypass stage order", lambda: record.write({"state": "1_done"}))
        rejects("cannot manually set blocking via API", lambda: record.write({"lab_is_blocked": True}))
        record.write({"stage_id": stages["Active"].id})
        check("New to Active allowed", record.stage_id == stages["Active"])
        rejects("cannot reverse Active to New", lambda: record.write({"stage_id": stages["New"].id}))
        record.write({"state": "1_done"})
        check("Done status synchronizes Complete", record.stage_id == stages["Complete"] and record.state == "1_done")
        rejects("cannot reopen completed request", lambda: record.write({"state": "01_in_progress"}))
        # --- Pending pickup (2026-11-21)
        rejects("cannot reverse Complete to Active", lambda: record.write({"stage_id": stages["Active"].id}))
        record.write({"stage_id": stages["Pending pickup"].id})
        check("Complete to Pending pickup allowed, still done", record.stage_id == stages["Pending pickup"] and record.state == "1_done")
        record.write({"state": "1_done"})
        check("Done on a Pending pickup request keeps the stage", record.stage_id == stages["Pending pickup"])
        rejects("cannot reverse Pending pickup to Complete", lambda: record.write({"stage_id": stages["Complete"].id}))
        rejects("cannot reopen Pending pickup request", lambda: record.write({"state": "01_in_progress"}))
        skipper = request("Skip check")
        skipper.write({"stage_id": stages["Active"].id})
        rejects("cannot skip Active to Pending pickup", lambda: skipper.write({"stage_id": stages["Pending pickup"].id}))
        check("sample-count completion condition remains deferred", not record.child_ids)
        another = request()
        rejects("mixed batch rejects skipping for every record", lambda: (record | another).write({"stage_id": stages["Complete"].id}))
        check("batch rejection leaves New unchanged", another.stage_id == stages["New"])
        eq = env["maintenance.equipment"].create({"name": "Workflow check equipment", "x_condition": "available", "equipment_assign_to": "other"})
        another.write({"x_equipment_id": eq.id})
        another.write({"stage_id": stages["Active"].id})
        eq.write({"x_condition": "under_repair"})
        check("equipment change automatically blocks assigned request", another.lab_is_blocked and "Under repair" in another.lab_block_reason)
        check("blocked request stays Active", another.stage_id == stages["Active"])
        rejects("unavailable equipment prevents completion", lambda: another.write({"stage_id": stages["Complete"].id}))
        eq.write({"x_condition": "available"})
        check("equipment repair automatically clears blocking", not another.lab_is_blocked)
        eq.write({"active": False})
        check("archived equipment blocks work", another.lab_is_blocked)
        eq.write({"active": True})
        check("unarchiving clears blocking", not another.lab_is_blocked)
        sample = task_model.create({"name": "Workflow sample", "project_id": project.id,
                                   "parent_id": another.id, "x_equipment_id": eq.id})
        another.write({"x_equipment_id": False})
        eq.write({"x_condition": "under_repair"})
        check("unfinished sample equipment blocks parent", another.lab_is_blocked)
        eq.write({"x_condition": "available"})
        sample.write({"x_scan": True, "x_export": True, "x_email": True})
        check("three toggles without Build in do not complete sample", sample.stage_id != stages["Complete"])
        sample.write({"x_build_in": True})
        check("sample checklist (with Build in) completes sample", sample.stage_id == stages["Complete"] and sample.state == "1_done")
        eq.write({"x_condition": "out_of_service"})
        check("completed sample equipment does not block parent", not another.lab_is_blocked)
        eq.write({"x_condition": "available"})
        sample.write({"x_email": False})
        check("sample checklist can reopen sample", sample.stage_id == stages["Active"])
        # The dashboard live path and filter must agree with the stored condition.
        eq.write({"x_condition": "under_repair"})
        env.flush_all()
        data = env["lab.dashboard"]._data_from_orm(__import__("datetime").datetime.utcnow())
        check("dashboard reports the structured block", any(r["id"] == another.id and r["blocked_reason"] for r in data["attention"]))
        check("blocking filter finds the same request", bool(task_model.search([("id", "=", another.id), ("lab_is_blocked", "=", True)])))
        check("dashboard counts Pending pickup, not as open", data["counts"]["pending_pickup"] >= 1
              and not any(r["id"] == record.id for r in data["attention"] + data["due14"]))
        eq.write({"x_condition": "available"})

        # --- Item 3: equipment already on a task stays there when it breaks (only the blocking flag changes)
        kept_eq = env["maintenance.equipment"].create({"name": "Workflow kept equipment", "x_condition": "available", "equipment_assign_to": "other"})
        held = request("Equipment kept check")
        held.write({"x_equipment_id": kept_eq.id})
        held_sample = task_model.create({"name": "Equipment kept sample", "project_id": project.id, "parent_id": held.id})
        check("sample inherits request equipment", held_sample.x_equipment_id == kept_eq)
        for condition in ("under_repair", "out_of_service"):
            kept_eq.write({"x_condition": condition})
            env.flush_all()
            env.invalidate_all()
            check(f"equipment stays on request and sample when {condition}",
                  held.x_equipment_id == kept_eq and held_sample.x_equipment_id == kept_eq and held.lab_is_blocked)

        # --- Item 4: unavailable equipment can be assigned and is labelled in the dropdown
        check("unavailable equipment label shows condition", kept_eq.display_name.endswith("(Out of service)"))
        kept_eq.write({"x_condition": "under_repair"})
        check("label follows condition change", kept_eq.display_name.endswith("(Under repair)"))
        check("available equipment label is clean", "(" not in eq.display_name)
        fresh = request("Assign unavailable equipment check")
        fresh.write({"x_equipment_id": kept_eq.id})
        check("unavailable equipment can be put on a request", fresh.x_equipment_id == kept_eq and fresh.lab_is_blocked)
        down_sample = task_model.create({"name": "Unavailable equipment sample", "project_id": project.id,
                                        "parent_id": fresh.id, "x_equipment_id": kept_eq.id})
        check("unavailable equipment can be put on a sample", down_sample.x_equipment_id == kept_eq)
        if hasattr(env["maintenance.equipment"], "name_search"):
            found = env["maintenance.equipment"].name_search("Workflow kept equipment")
            check("dropdown search lists unavailable equipment with its label",
                  any(rid == kept_eq.id and "(Under repair)" in label for rid, label in found))

        # --- Items 1/6: numeric time on samples, stored roll-up on the request
        check("old text parses", parse_hours("1.5 h") == 1.5 and parse_hours("90 min") == 1.5 and parse_hours("1:30") == 1.5
              and parse_hours("abc") is None)
        held_sample.write({"lab_time_taken": 1.5})
        extra = task_model.create({"name": "Time sample", "project_id": project.id, "parent_id": held.id, "lab_time_taken": 0.25})
        check("request total sums sample time", abs(held.lab_time_total - 1.75) < 1e-6)
        extra.write({"active": False})
        check("archived sample time leaves the total", abs(held.lab_time_total - 1.5) < 1e-6)
        rejects("negative time rejected", lambda: held_sample.write({"lab_time_taken": -1}))
        rejects("request total time cannot be set by hand", lambda: held.write({"lab_time_total": 99}))
        check("request total time unchanged after rejected write", abs(held.lab_time_total - 1.5) < 1e-6)

        # --- Items 7/8: intern adds samples to own requests; Lab Home shows only own work
        intern = env["res.users"].with_context(no_reset_password=True, mail_create_nosubscribe=True).create({
            "name": "Workflow check intern", "login": "workflow-check-intern@lab.invalid",
            "group_ids": [(6, 0, [env.ref("base.group_user").id, env.ref("lab_tracker.group_lab_intern").id])],
        })
        mine = request("Intern request check")
        mine.write({"user_ids": [(6, 0, intern.ids)]})
        theirs = request("Other request check")
        theirs.write({"user_ids": [(6, 0, admin.ids)]})
        env.flush_all()
        as_intern = env["project.task"].with_user(intern).sudo(False).with_context(
            tracking_disable=True, mail_create_nosubscribe=True, mail_notrack=True)
        own = as_intern.create({"name": "Intern sample check", "project_id": project.id, "parent_id": mine.id})
        check("intern can create a sample under an assigned request", own.parent_id == mine)
        own.write({"x_scan": True, "lab_time_taken": 0.5})
        check("intern can edit the new sample", own.x_scan and own.lab_time_taken == 0.5)
        # 2026-11-22: assignee guard is in the model (no automation, so no ERROR log line on a denial)
        check("old intern assignee automation removed", not env["base.automation"].with_context(active_test=False).search_count(
            [("name", "=", "Lab intern: cannot change assignee")]))
        rejects("intern cannot change a sample's assignees", lambda: own.write({"user_ids": [(6, 0, admin.ids)]}))
        rejects("intern cannot add an assignee to a request", lambda: as_intern.browse(mine.id).write({"user_ids": [(4, admin.id)]}))
        rejects("intern cannot remove themselves from a request", lambda: as_intern.browse(mine.id).write({"user_ids": [(6, 0, [])]}))
        own.write({"user_ids": [(6, 0, own.user_ids.ids)], "x_export": True})
        check("intern write with unchanged assignees is allowed", own.x_export)
        check("admin can still reassign", bool(mine.write({"user_ids": [(4, admin.id)]})) and admin in mine.user_ids)
        mine.write({"user_ids": [(6, 0, intern.ids)]})
        rejects("intern cannot create a top-level request", lambda: as_intern.create({
            "name": "Intern request denied", "project_id": project.id, "user_ids": [(6, 0, intern.ids)]}))
        rejects("intern cannot create a sample under someone else's request", lambda: as_intern.create({
            "name": "Intern sample denied", "project_id": project.id, "parent_id": theirs.id}))
        rejects("intern cannot create a sample under a sample", lambda: as_intern.create({
            "name": "Intern nested sample denied", "project_id": project.id, "parent_id": own.id}))
        rejects("intern cannot assign a new sample to someone else", lambda: as_intern.create({
            "name": "Intern sample other assignee", "project_id": project.id, "parent_id": mine.id,
            "user_ids": [(6, 0, admin.ids)]}))
        rejects("intern cannot delete a sample", lambda: own.unlink())
        rejects("intern cannot delete a request", lambda: as_intern.browse(mine.id).unlink())
        dash = env["lab.dashboard"].with_user(intern).sudo(False).get_data()
        visible_open = as_intern.search_count([("project_id", "=", project.id), ("parent_id", "=", False),
                                               ("stage_id.name", "not in", ["Complete", "Pending pickup"])])
        check("intern opens Lab Home with own-work scope", dash["scope"] == "assigned" and dash["source"] == "live")
        check("intern Lab Home counts only readable requests", dash["counts"]["open"] == visible_open
              and not any(r["id"] == theirs.id for r in dash["attention"] + dash["due14"]))
        print(f"{len(passed)} workflow checks passed")
    finally:
        env.cr.rollback()
        print("All workflow check changes rolled back.")


check_workflow(env)
