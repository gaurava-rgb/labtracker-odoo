"""Lab Home dashboard data.

get_data() returns the five tile counts, the three lists and the click-through domains.
Numbers are read with plain SQL from the Postgres read replica (DSN in env LAB_DASH_DSN,
set by docker-compose). If the DSN is missing or the replica cannot be reached, the same
numbers are computed on the primary through the ORM and the payload says source="live".
Definitions mirror the saved filters created by scripts/odoo_lab_rules.py:
  request   = top-level active task in project "Lab requests" (parent_id is NULL)
  open      = request whose stage is not "Complete"
  unassigned= open with no row in project_task_user_rel
  overdue   = open with date_deadline < now (UTC)
  blocked   = request whose stage is "Blocked"
  equipment down = maintenance_equipment with x_condition != 'available' (NULL counts as down)
"""
import logging
import os
from datetime import timedelta

from odoo import api, fields, models
from odoo.exceptions import AccessError

_logger = logging.getLogger(__name__)

PROJECT_NAME = "Lab requests"
ALLOWED_GROUPS = ("lab_tracker.group_lab_manager", "lab_tracker.group_lab_coordinator", "base.group_system")
OPEN_REQUEST_STATES = ("done", "cancelled")  # maintenance.request states that are NOT open


class LabDashboard(models.AbstractModel):
    _name = "lab.dashboard"
    _description = "Lab Home dashboard"

    # ------------------------------------------------------------ access
    def _check_dashboard_access(self):
        user = self.env.user
        for xmlid in ALLOWED_GROUPS:
            try:
                if user.has_group(xmlid):
                    return
            except Exception:  # group not defined on this database
                continue
        raise AccessError(self.env._("Only lab managers and coordinators can open the Lab Home dashboard."))

    # ------------------------------------------------------------ public entry point
    @api.model
    def get_data(self):
        self._check_dashboard_access()
        now = fields.Datetime.now()
        data = None
        dsn = os.environ.get("LAB_DASH_DSN")
        if dsn:
            try:
                data = self._data_from_replica(dsn, now)
                data["source"] = "replica"
            except Exception as exc:  # noqa: BLE001 - any failure means "use live data"
                _logger.warning("lab_dashboard: replica unavailable (%s), falling back to live ORM", exc.__class__.__name__)
                data = None
        if data is None:
            data = self._data_from_orm(now)
            data["source"] = "live"
        data["as_of"] = fields.Datetime.to_string(now)
        data["domains"] = self._domains(data["project_id"], now)
        return data

    # ------------------------------------------------------------ click-through domains (same as saved filters)
    @api.model
    def _domains(self, project_id, now):
        base = [("project_id", "=", project_id), ("parent_id", "=", False)]
        open_ = base + [("stage_id.name", "!=", "Complete")]
        now_s = fields.Datetime.to_string(now)
        in14 = fields.Datetime.to_string(now + timedelta(days=14))
        return {
            "open": open_,
            "unassigned": open_ + [("user_ids", "=", False)],
            "overdue": open_ + [("date_deadline", "<", now_s)],
            "blocked": base + [("stage_id.name", "=", "Blocked")],
            "due14": open_ + [("date_deadline", ">=", now_s), ("date_deadline", "<=", in14)],
            "attention": open_ + ["|", "|", ("date_deadline", "<", now_s), ("stage_id.name", "=", "Blocked"), ("user_ids", "=", False)],
            "equipment_down": [("x_condition", "!=", "available")],
        }

    # ------------------------------------------------------------ replica (plain read-only SQL)
    SQL_REQUESTS = """
        WITH p AS (
            SELECT id FROM project_project
            WHERE EXISTS (SELECT 1 FROM jsonb_each_text(name) WHERE value = %(project)s)
            ORDER BY id LIMIT 1
        ),
        req AS (
            SELECT t.id, t.name, t.date_deadline,
                   (SELECT value FROM jsonb_each_text(s.name) LIMIT 1) AS stage,
                   EXISTS (SELECT 1 FROM jsonb_each_text(s.name) WHERE value = 'Complete') AS complete,
                   EXISTS (SELECT 1 FROM jsonb_each_text(s.name) WHERE value = 'Blocked') AS blocked,
                   NOT EXISTS (SELECT 1 FROM project_task_user_rel r WHERE r.task_id = t.id) AS unassigned,
                   (t.date_deadline IS NOT NULL AND t.date_deadline < %(now)s) AS overdue,
                   (t.date_deadline >= %(now)s AND t.date_deadline <= %(in14)s) AS due14,
                   (SELECT string_agg(rp.name, ', ' ORDER BY rp.name)
                      FROM project_task_user_rel r
                      JOIN res_users u ON u.id = r.user_id
                      JOIN res_partner rp ON rp.id = u.partner_id
                     WHERE r.task_id = t.id) AS assignees
            FROM project_task t
            JOIN project_task_type s ON s.id = t.stage_id
            WHERE t.project_id = (SELECT id FROM p) AND t.parent_id IS NULL AND t.active
        )
        SELECT (SELECT id FROM p) AS project_id, req.* FROM req
    """
    SQL_EQUIPMENT = """
        SELECT e.id,
               (SELECT value FROM jsonb_each_text(e.name) LIMIT 1) AS name,
               e.serial_no, e.x_condition,
               (SELECT count(*) FROM maintenance_request mr
                  LEFT JOIN maintenance_stage ms ON ms.id = mr.stage_id
                 WHERE mr.equipment_id = e.id
                   AND COALESCE(ms.fold, false) = false
                   AND COALESCE(mr.state, 'normal') NOT IN %(closed)s) AS open_requests
        FROM maintenance_equipment e
        WHERE e.active
        ORDER BY (COALESCE(e.x_condition, '') <> 'available') DESC, name
    """

    @api.model
    def _data_from_replica(self, dsn, now):
        import psycopg2
        import psycopg2.extras

        params = {"project": PROJECT_NAME, "now": now, "in14": now + timedelta(days=14), "closed": OPEN_REQUEST_STATES}
        conn = psycopg2.connect(dsn)
        try:
            conn.set_session(readonly=True, autocommit=True)
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(self.SQL_REQUESTS, params)
                reqs = cur.fetchall()
                cur.execute(self.SQL_EQUIPMENT, params)
                equipment = cur.fetchall()
        finally:
            conn.close()

        project_id = reqs[0]["project_id"] if reqs else self._project_id()
        rows = [self._req_row(r) for r in reqs]
        return self._assemble(project_id, rows, [self._eq_row(e) for e in equipment])

    # ------------------------------------------------------------ live (ORM on the primary)
    @api.model
    def _project_id(self):
        project = self.env["project.project"].search([("name", "=", PROJECT_NAME)], limit=1)
        return project.id or 0

    @api.model
    def _data_from_orm(self, now):
        project_id = self._project_id()
        in14 = now + timedelta(days=14)
        tasks = self.env["project.task"].search_read(
            [("project_id", "=", project_id), ("parent_id", "=", False)],
            ["name", "date_deadline", "stage_id", "user_ids"], order="date_deadline, id",
        )
        stage_names = {s["id"]: s["name"] for s in self.env["project.task.type"].search_read(
            [("id", "in", [t["stage_id"][0] for t in tasks if t["stage_id"]])], ["name"])}
        user_ids = {u for t in tasks for u in t["user_ids"]}
        user_names = {u["id"]: u["name"] for u in self.env["res.users"].search_read([("id", "in", list(user_ids))], ["name"])}
        rows = []
        for t in tasks:
            stage = stage_names.get(t["stage_id"][0], "") if t["stage_id"] else ""
            dl = t["date_deadline"]
            rows.append(self._req_row({
                "id": t["id"], "name": t["name"], "date_deadline": dl, "stage": stage,
                "complete": stage == "Complete", "blocked": stage == "Blocked",
                "unassigned": not t["user_ids"],
                "overdue": bool(dl) and dl < now,
                "due14": bool(dl) and now <= dl <= in14,
                "assignees": ", ".join(sorted(user_names.get(u, "") for u in t["user_ids"])),
            }))
        equipment = self.env["maintenance.equipment"].search_read([], ["name", "serial_no", "x_condition"], order="name")
        open_counts = {}
        if equipment:
            for eq, count in self.env["maintenance.request"]._read_group(
                [("equipment_id", "in", [e["id"] for e in equipment]), ("stage_id.fold", "=", False),
                 ("state", "not in", list(OPEN_REQUEST_STATES))], ["equipment_id"], ["__count"]):
                if eq:
                    open_counts[eq.id] = count
        eq_rows = [self._eq_row({**e, "open_requests": open_counts.get(e["id"], 0)}) for e in equipment]
        eq_rows.sort(key=lambda e: ((e["condition"] == "available"), e["name"]))
        return self._assemble(project_id, rows, eq_rows)

    # ------------------------------------------------------------ shared shaping
    @staticmethod
    def _req_row(r):
        dl = r.get("date_deadline")
        flags = [f for f in ("overdue", "blocked", "unassigned") if r.get(f)]
        return {
            "id": r["id"], "name": r["name"], "stage": r.get("stage") or "",
            "assignees": r.get("assignees") or "", "deadline": dl.strftime("%Y-%m-%d") if dl else "",
            "open": not r.get("complete"), "blocked": bool(r.get("blocked")), "unassigned": bool(r.get("unassigned")),
            "overdue": bool(r.get("overdue")), "due14": bool(r.get("due14")), "flags": flags,
        }

    @staticmethod
    def _eq_row(e):
        return {"id": e["id"], "name": e.get("name") or "", "serial_no": e.get("serial_no") or "",
                "condition": e.get("x_condition") or "", "open_requests": int(e.get("open_requests") or 0)}

    @staticmethod
    def _assemble(project_id, rows, equipment):
        open_rows = [r for r in rows if r["open"]]
        attention = [r for r in open_rows if r["overdue"] or r["blocked"] or r["unassigned"]]
        attention.sort(key=lambda r: (not r["overdue"], r["deadline"] or "9999", r["id"]))
        due14 = sorted((r for r in open_rows if r["due14"]), key=lambda r: (r["deadline"], r["id"]))
        return {
            "project_id": project_id,
            "counts": {
                "open": len(open_rows),
                "unassigned": sum(1 for r in open_rows if r["unassigned"]),
                "overdue": sum(1 for r in open_rows if r["overdue"]),
                "blocked": sum(1 for r in rows if r["blocked"]),
                "equipment_down": sum(1 for e in equipment if e["condition"] != "available"),
            },
            "attention": attention,
            "due14": due14,
            "equipment": equipment,
        }
