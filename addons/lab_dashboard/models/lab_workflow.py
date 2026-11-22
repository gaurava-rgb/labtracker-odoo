"""Ordered request workflow, with blocking derived from equipment data.

Also (20.0.2.2.0): numeric sample time with a request roll-up, the "Pending pickup" stage after Complete,
intern-created samples (assignee guard), and equipment names that show a non-available condition.
2026-11-22: interns cannot change assignees (Python guard in write, replaces an automation rule); the request
total time cannot be written by hand.
"""
import logging
import re

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

_logger = logging.getLogger(__name__)

# (xml id suffix, stage name) in workflow order. Complete = lab work done; Pending pickup = awaiting client pickup.
LAB_STAGES = (("new", "New"), ("active", "Active"), ("complete", "Complete"), ("pending_pickup", "Pending pickup"))
STAGE_NAMES = tuple(name for _key, name in LAB_STAGES)
DONE_STAGE_NAMES = ("Complete", "Pending pickup")  # task state 1_done; no longer "open" work
INTERN_GROUP = "lab_tracker.group_lab_intern"
WORKFLOW_TEXT = "New → Active → Complete → Pending pickup"

_UNIT_RE = re.compile(
    r"(?:(?P<h>\d+(?:\.\d+)?)\s*(?:hours|hour|hrs|hr|h)(?![a-z])\.?)?\s*(?:and\s*)?"
    r"(?:(?P<m>\d+(?:\.\d+)?)\s*(?:minutes|minute|mins|min|m)(?![a-z])\.?)?"
)


def parse_hours(text):
    """Old free-text "time spent" to hours: "1.5 h", "1.5", "2 hours", "90 min", "1h 30m", "1:30". None if unclear."""
    if not text:
        return None
    s = re.sub(r"\s+", " ", str(text).strip().lower().replace(",", "."))
    if not s:
        return None
    if re.fullmatch(r"\d+(?:\.\d+)?", s):
        return round(float(s), 4)
    m = re.fullmatch(r"(\d+):([0-5]?\d)", s)
    if m:
        return round(int(m.group(1)) + int(m.group(2)) / 60, 4)
    m = _UNIT_RE.fullmatch(s)
    if m and (m.group("h") or m.group("m")):
        return round(float(m.group("h") or 0) + float(m.group("m") or 0) / 60, 4)
    return None


def blocking_dependencies(model):
    # Custom x_ fields are provisioned after the add-on's first installation.
    # Registry reloads discover their dependencies once those fields exist.
    paths = (
        "project_id", "stage_id", "parent_id", "active", "child_ids.active", "child_ids.stage_id",
        "x_equipment_id", "x_equipment_id.name", "x_equipment_id.x_condition", "x_equipment_id.active",
        "parent_id.x_equipment_id", "parent_id.x_equipment_id.x_condition", "parent_id.x_equipment_id.active",
        "child_ids.x_equipment_id", "child_ids.x_equipment_id.x_condition", "child_ids.x_equipment_id.active",
    )
    result = []
    for path in paths:
        current = model
        for part in path.split("."):
            field = current._fields.get(part)
            if field is None:
                break
            if field.relational:
                current = model.env[field.comodel_name]
        else:
            result.append(path)
    return result


class LabTask(models.Model):
    _inherit = "project.task"

    lab_is_blocked = fields.Boolean(string="Blocked", compute="_compute_lab_blocking", store=True, readonly=True)
    lab_block_reason = fields.Char(string="Blocking reason", compute="_compute_lab_blocking", store=True, readonly=True)
    # Time: entered per sample in hours (float_time widget shows 1.5 as 01:30); the request shows the sum of its samples.
    lab_time_taken = fields.Float(string="Time taken (h)", digits=(16, 2), copy=False,
                                  help="Hours spent on this sample. Entered by hand; nothing is timed automatically.")
    lab_time_total = fields.Float(string="Total time taken (h)", digits=(16, 2), compute="_compute_lab_time_total",
                                  store=True, readonly=True, help="Sum of the time taken on this request's active samples.")

    @api.depends("child_ids.lab_time_taken", "child_ids.active")
    def _compute_lab_time_total(self):
        for task in self:
            task.lab_time_total = sum(task.child_ids.filtered("active").mapped("lab_time_taken"))

    @api.constrains("lab_time_taken")
    def _check_lab_time_taken(self):
        if any(task.lab_time_taken < 0 for task in self):
            raise ValidationError("Time taken cannot be negative.")

    def _lab_project(self):
        return self.env.ref("lab_tracker.project_lab_requests", raise_if_not_found=False) or self.env[
            "project.project"
        ].sudo().search([("name", "=", "Lab requests")], limit=1)

    def _lab_stages(self):
        project = self._lab_project()
        result = {}
        for key, name in LAB_STAGES:
            result[name] = self.env.ref(f"lab_tracker.stage_{key}", raise_if_not_found=False) or self.env[
                "project.task.type"
            ].sudo().search([("name", "=", name), ("project_ids", "in", project.ids)], limit=1)
        return result

    def _lab_done_stages(self, stages=None):
        stages = stages or self._lab_stages()
        return self.env["project.task.type"].union(stages[name] for name in DONE_STAGE_NAMES if stages.get(name))

    def _lab_effective_equipment(self):
        self.ensure_one()
        own = self.x_equipment_id if "x_equipment_id" in self._fields else self.env["maintenance.equipment"]
        if not own and self.parent_id and "x_equipment_id" in self._fields:
            own = self.parent_id.x_equipment_id
        return own

    @api.depends(blocking_dependencies)
    def _compute_lab_blocking(self):
        project = self._lab_project()
        done = self._lab_done_stages()
        for task in self:
            reasons = []
            if project and task.project_id == project and task.active and task.stage_id not in done:
                relevant = task | task.child_ids.filtered(lambda child: child.active and child.stage_id not in done)
                for item in relevant:
                    eq = item._lab_effective_equipment()
                    if not eq:
                        continue
                    condition = eq.x_condition if "x_condition" in eq._fields else "available"
                    if not eq.active or condition != "available":
                        label = "Archived" if not eq.active else dict(eq._fields["x_condition"].selection).get(condition, "Unknown condition")
                        reason = f"{eq.name}: {label}"
                        if reason not in reasons:
                            reasons.append(reason)
            task.lab_is_blocked = bool(reasons)
            task.lab_block_reason = "; ".join(reasons) or False

    @api.model_create_multi
    def create(self, vals_list):
        project = self._lab_project()
        stages = self._lab_stages() if project else {}
        for values in vals_list:
            # Samples retain their checklist-driven lifecycle; order is enforced
            # for top-level Lab requests, not personal tasks or other projects.
            project_id = values.get("project_id", self.env.context.get("default_project_id"))
            parent_id = values.get("parent_id", self.env.context.get("default_parent_id"))
            if project_id == project.id and not parent_id and stages.get("New"):
                stage_id = values.get("stage_id", self.env.context.get("default_stage_id"))
                state = values.get("state", self.env.context.get("default_state", "01_in_progress"))
                if stage_id and stage_id != stages["New"].id or state != "01_in_progress":
                    raise UserError(f"New Lab requests must start at New. Then follow {WORKFLOW_TEXT}.")
                values.update(stage_id=stages["New"].id, state="01_in_progress")
        records = super().create(vals_list)
        # default_get/onchange logic can infer a project from active_model and
        # active_id without placing project_id in the caller's values.
        for record in records:
            if project and record.project_id == project and not record.parent_id:
                if record.stage_id != stages["New"] or record.state != "01_in_progress":
                    raise UserError(f"New Lab requests must start at New. Then follow {WORKFLOW_TEXT}.")
        records._lab_check_intern_assignees()
        return records

    def _lab_check_intern_assignees(self):
        """Interns may add samples to their requests (ir.access row from init/lab_roles.py), but a new sample may only
        be assigned to the intern or to people already on the parent request; the assignee guard covers later edits."""
        if self.env.su:
            return
        group = self.env.ref(INTERN_GROUP, raise_if_not_found=False)
        if not group or not self.env.user.has_group(INTERN_GROUP):
            return
        for record in self:
            if record.parent_id and record.user_ids - self.env.user - record.parent_id.user_ids:
                raise AccessError("Interns can assign a new sample only to themselves or to the request's assignees.")

    def _lab_user_is_intern(self):
        return not self.env.su and bool(self.env.ref(INTERN_GROUP, raise_if_not_found=False)) \
            and self.env.user.has_group(INTERN_GROUP)

    def write(self, values):
        """Intern assignee guard (2026-11-22, replaces the automation "Lab intern: cannot change assignee", which
        logged an ERROR on every denial): an intern write that really changes user_ids is rejected with a clean
        UserError. Writes that leave the assignees unchanged pass, like the old on-change automation."""
        if "user_ids" in values and self._lab_user_is_intern():
            before = {task.id: set(task.user_ids.ids) for task in self}
            result = self._lab_write(values)
            # read back as superuser: removing themselves can take the record out of the intern's read scope
            if any(set(task.user_ids.ids) != before.get(task.id, set()) for task in self.sudo()):
                raise UserError("Interns cannot change who a request or sample is assigned to.")
            return result
        return self._lab_write(values)

    def _lab_write(self, values):
        values = dict(values)
        if {"lab_is_blocked", "lab_block_reason"} & values.keys() and not self.env.su:
            # readonly is only a UI property; reject direct API writes as well.
            raise UserError("Blocking is calculated from equipment data and cannot be set manually.")
        if "lab_time_total" in values and not self.env.su:
            # 2026-11-22: the request total is the sum of its samples; the compute stores it without calling write().
            raise UserError("Total time taken is the sum of the samples' time and cannot be set manually.")
        project = self._lab_project()
        requests = self.filtered(lambda task: project and task.project_id == project and not task.parent_id)
        if requests and {"stage_id", "state", "project_id", "parent_id"} & values.keys():
            self.env.cr.execute("SELECT id FROM project_task WHERE id IN %s ORDER BY id FOR UPDATE", [tuple(requests.ids)])
            requests.invalidate_recordset(["stage_id", "state"])
            stages = self._lab_stages()
            order = [stages[name].id for name in STAGE_NAMES if stages.get(name)]
            done_ids = set(self._lab_done_stages(stages).ids)
            if "project_id" in values and values["project_id"] != project.id or values.get("parent_id"):
                raise UserError("A Lab request cannot be moved out of its workflow or converted to a sample.")
            state = values.get("state")
            if state is not None and state not in ("01_in_progress", "1_done"):
                raise UserError(f"Lab requests use {WORKFLOW_TEXT}. Blocking is calculated separately.")
            target = values.get("stage_id")
            if target is None and state == "1_done":
                # "Mark as done" means the lab work is complete: Active -> Complete. A request that is already
                # Complete or Pending pickup keeps its stage (no step backwards).
                finished = requests.filtered(lambda request: request.stage_id.id in done_ids)
                if finished:
                    rest = self - finished
                    result = super(LabTask, finished).write(values)
                    if rest:
                        rest._lab_write(values)
                    return result
                target = stages["Complete"].id
            if target is not None:
                if target not in order:
                    raise UserError("Choose New, Active, Complete, or Pending pickup. Blocked is not a workflow stage.")
                for request in requests:
                    current = request.stage_id.id
                    if target != current and (current not in order or order.index(target) != order.index(current) + 1):
                        raise UserError(f"Lab requests must follow {WORKFLOW_TEXT}; skipping or reversing stages is not allowed.")
                    if target != current:
                        request.sudo()._compute_lab_blocking()
                        if request.lab_is_blocked:
                            raise UserError(f"Cannot progress this request: {request.lab_block_reason}")
                values["stage_id"] = target
                values["state"] = "1_done" if target in done_ids else "01_in_progress"
            elif state == "01_in_progress" and any(request.stage_id.id in done_ids for request in requests):
                raise UserError("Completed Lab requests cannot be reopened in this workflow.")
        elif {"project_id", "parent_id"} & values.keys():
            # Reclassifying an existing sample/personal task must not bypass New.
            for task in self:
                target_project = values.get("project_id", task.project_id.id)
                target_parent = values.get("parent_id", task.parent_id.id)
                if project and target_project == project.id and not target_parent and task not in requests:
                    raise UserError("Create a new Lab request instead of converting another task into one.")
        return super().write(values)

    @api.model
    def _lab_setup_workflow(self):
        """Upgrade existing records without exposing a context-based API bypass."""
        if not self.env.su:
            raise UserError("Only the module migration can normalize the Lab workflow.")
        project = self._lab_project()
        if not project:
            return
        metadata = self.env["ir.model.data"]

        def remember(name, record):
            marker = metadata.search([("module", "=", "lab_tracker"), ("name", "=", name)])
            values = {"model": record._name, "res_id": record.id, "noupdate": True}
            if marker:
                marker.write(values)
            else:
                metadata.create(dict(values, module="lab_tracker", name=name))

        remember("project_lab_requests", project)
        for sequence, (key, name) in enumerate(LAB_STAGES, 1):
            stage = self.env["project.task.type"].search([("name", "=", name), ("project_ids", "in", project.ids)], limit=1)
            if not stage:
                stage = self.env["project.task.type"].create({"name": name, "project_ids": [(4, project.id)]})
            # Nothing folded: Complete and Pending pickup are both working columns now (see WORKFLOW.md).
            stage.write({"sequence": sequence, "fold": False})
            remember("stage_" + key, stage)
        stages = self._lab_stages()
        tasks = self.with_context(active_test=False).search([("project_id", "=", project.id)])
        blocked = tasks.filtered(lambda task: task.stage_id.name == "Blocked")
        if blocked:
            super(LabTask, blocked).write({"stage_id": stages["Active"].id, "state": "01_in_progress"})
        for stage in self.env["project.task.type"].search([("name", "=", "Blocked"), ("project_ids", "in", project.ids)]):
            stage.write({"project_ids": [(3, project.id)]})
            if not stage.project_ids and not self.with_context(active_test=False).search_count([("stage_id", "=", stage.id)]):
                stage.unlink()
        for name, stage in stages.items():
            top = tasks.filtered(lambda task: not task.parent_id and task.stage_id == stage)
            if top:
                super(LabTask, top).write({"state": "1_done" if name in DONE_STAGE_NAMES else "01_in_progress"})
        tasks._compute_lab_blocking()

    @api.model
    def _lab_migrate_time_spent(self):
        """One-off: copy the old free-text x_time_spent ("1.5 h") into lab_time_taken. Only fills empty values,
        so it is safe to run again. Text that cannot be read as hours stays in x_time_spent and gets a chatter note."""
        if not self.env.su:
            raise UserError("Only the module migration can convert old time values.")
        cr = self.env.cr
        cr.execute("SELECT 1 FROM information_schema.columns WHERE table_name = 'project_task' AND column_name = 'x_time_spent'")
        if not cr.fetchone():
            return {"converted": 0, "unparsed": 0}
        cr.execute("""SELECT id, x_time_spent FROM project_task
                       WHERE COALESCE(btrim(x_time_spent), '') <> '' AND COALESCE(lab_time_taken, 0) = 0""")
        converted = unparsed = 0
        tasks = self.with_context(active_test=False, tracking_disable=True, mail_notrack=True)
        for task_id, text in cr.fetchall():
            task = tasks.browse(task_id)
            hours = parse_hours(text)
            if hours is None:
                unparsed += 1
                try:
                    task.message_post(body=f"Old 'Time spent' text could not be converted to hours: {text}",
                                      message_type="comment", subtype_xmlid="mail.mt_note")
                except Exception:  # noqa: BLE001 - a missing note must not stop the upgrade
                    _logger.warning("lab_dashboard: could not post time note on task %s", task_id)
                continue
            if hours:
                super(LabTask, task).write({"lab_time_taken": hours})
            converted += 1
        _logger.info("lab_dashboard: time spent migration converted=%s unparsed=%s", converted, unparsed)
        return {"converted": converted, "unparsed": unparsed}


def equipment_name_dependencies(model):
    # x_condition is a provisioned (manual) field; see blocking_dependencies above.
    return [name for name in ("name", "serial_no", "x_condition") if name in model._fields]


class LabEquipmentName(models.Model):
    """Dropdowns list all equipment; a non-available item is labelled, e.g. "Centrifuge (Under repair)"."""
    _inherit = "maintenance.equipment"

    @api.depends(equipment_name_dependencies)
    def _compute_display_name(self):
        super()._compute_display_name()
        if "x_condition" not in self._fields:
            return
        labels = dict(self._fields["x_condition"].selection or [])
        for equipment in self:
            condition = equipment.x_condition
            if condition == "available" or not equipment.display_name:
                continue
            label = labels.get(condition, "Condition unknown") if condition else "Condition unknown"
            equipment.display_name = f"{equipment.display_name} ({label})"
