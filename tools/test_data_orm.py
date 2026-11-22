"""Executed by odoo shell with env and TEST_OPTIONS supplied by test_data.py.

One transaction, a DB advisory lock, exact external-ID ownership, no production
sequence reset, no external mail, and refusal to cascade into unowned business data.
"""
from collections import Counter
from datetime import datetime, timedelta

from odoo.exceptions import AccessError, UserError

MODULE = "labtracker_test_data"
PREFIX = "[TEST] "
VERSION = "1"
ORDER = ("project.task", "maintenance.request", "crm.lead", "maintenance.equipment",
         "res.users", "res.partner")


class Fixture:
    def __init__(self, environment):
        self.env = environment["res.users"].sudo().with_context(
            active_test=False, tracking_disable=True, mail_create_nosubscribe=True,
            mail_notrack=True, no_reset_password=True,
        ).env
        self.meta = self.env["ir.model.data"]

    def markers(self):
        return self.meta.search([("module", "=", MODULE)])

    def record(self, key):
        marker = self.meta.search([("module", "=", MODULE), ("name", "=", key)])
        return self.env[marker.model].browse(marker.res_id).exists() if marker else None

    def own(self, key, record):
        self.meta.create({"module": MODULE, "name": key, "model": record._name,
                          "res_id": record.id, "noupdate": True})
        return record

    def create(self, key, model, values):
        return self.own(key, self.env[model].create(values))

    def seed(self, credentials):
        if self.markers():
            if not self.record("fixture_version"):
                raise UserError("Incomplete test fixture markers; clean the old fixture before seeding.")
            print("Test fixture already exists. No records or passwords changed; clean first to reseed.")
            return False
        project = self.env["project.project"].search([("name", "=", "Lab requests")])
        if len(project) != 1:
            raise UserError("Expected exactly one provisioned 'Lab requests' project.")
        stages = {}
        for name in ("New", "Active", "Complete"):
            stage = self.env["project.task.type"].search([("name", "=", name), ("project_ids", "in", project.ids)])
            if len(stage) != 1:
                raise UserError(f"Missing or ambiguous stage: {name}")
            stages[name] = stage.id
        users = {}
        for role in ("manager", "coordinator", "intern"):
            login = credentials[role]["login"]
            if self.env["res.users"].search_count([("login", "=", login)]):
                raise UserError(f"Login {login} already exists without our ownership marker; refusing to overwrite it.")
            before_tasks = set(self.env["project.task"].search([]).ids)
            partner = self.create(f"user_partner_{role}", "res.partner", {"name": PREFIX + role.title(), "email": login})
            users[role] = self.create(f"user_{role}", "res.users", {
                "partner_id": partner.id, "login": login, "email": login,
                "password": credentials[role]["password"],
                "company_id": self.env.company.id, "company_ids": [(6, 0, self.env.company.ids)],
                "group_ids": [(6, 0, [self.env.ref("base.group_user").id,
                                       self.env.ref(f"lab_tracker.group_lab_{role}").id])],
            })
            # Odoo creates a personal 'Welcome ...!' task when a user is created.
            # This new user is uncommitted, so another transaction cannot create
            # tasks referencing them. Never claim a task that existed beforehand.
            for task in self.env["project.task"].search([("user_ids", "in", users[role].ids)]):
                if task.id not in before_tasks:
                    self.own(f"welcome_task_{role}_{task.id}", task)
        clients = []
        for i, name in enumerate(("Northstar Materials", "Riverbend Biotech", "Atlas Research")):
            client = self.create(f"client_{i}", "res.partner", {
                "name": PREFIX + name, "email": f"client{i}@lab.invalid", "ref": f"TEST-CLIENT-{i}",
                "is_company": True,
            })
            client.write({"is_company": True})
            clients.append(client)
        equipment = []
        category = self.env["maintenance.equipment.category"].search([("name", "=", "Lab instruments")], limit=1)
        for i, (name, condition) in enumerate((
            ("Electron microscope", "available"), ("Spectrometer", "available"),
            ("Centrifuge", "available"), ("Chromatograph", "under_repair"),
            ("Thermal analyzer", "out_of_service"),
        )):
            equipment.append(self.create(f"equipment_{i}", "maintenance.equipment", {
                "name": PREFIX + name, "serial_no": f"TEST-INSTRUMENT-{i}", "category_id": category.id,
                "equipment_assign_to": "other", "x_condition": condition,
            }))
        now = datetime.utcnow().replace(microsecond=0)
        cases = [
            ("Surface analysis overdue", "Active", -3, "intern"),
            ("Water quality awaiting assignment", "New", 3, None),
            ("Polymer equipment availability check", "Active", -1, "manager"),
            ("Protein assay due this week", "Active", 5, "coordinator"),
            ("Alloy report delivered", "Complete", -2, "manager"),
            ("Stability study next month", "New", 30, "coordinator"),
        ]
        for i, (name, stage, days, role) in enumerate(cases):
            request = self.create(f"request_{i}", "project.task", {
                "name": PREFIX + name, "project_id": project.id, "partner_id": clients[i % 3].id,
                "stage_id": stages["New"], "date_deadline": now + timedelta(days=days),
                "x_submitted": now.date() - timedelta(days=7), "x_save_location": f"/test-data/request-{i}",
                "x_equipment_id": equipment[2 if i == 2 else i % 2].id,
                "user_ids": [(6, 0, [users[role].id] if role else [])],
                "description": "<p>Synthetic Lab Tracker fixture. Safe to remove with clean-test-data.sh.</p>",
                "state": "01_in_progress",
            })
            if stage != "New":
                request.write({"stage_id": stages["Active"]})
            if stage == "Complete":
                request.write({"stage_id": stages["Complete"]})
            for j in range(2):
                done = stage == "Complete" or (i == 3 and j == 0)
                self.create(f"sample_{i}_{j}", "project.task", {
                    "name": PREFIX + f"Sample {i + 1}.{j + 1}", "project_id": project.id,
                    "parent_id": request.id, "partner_id": clients[i % 3].id,
                    "stage_id": stages["Complete" if done else "Active"],
                    "user_ids": [(6, 0, [users[role].id] if role else [])],
                    "x_scan": done or j == 0, "x_export": done, "x_email": done, "x_build_in": done,
                    "lab_time_taken": 1.5 if done else 0.5,
                })
        # A real data condition, not a stage or title, blocks the polymer request.
        equipment[2].write({"x_condition": "under_repair"})
        for i, eq in enumerate(equipment[3:]):
            self.create(f"maintenance_{i}", "maintenance.request", {
                "name": PREFIX + f"Inspect {eq.name}", "equipment_id": eq.id,
                "maintenance_type": "corrective", "schedule_date": now,
            })
        for i, client in enumerate(clients):
            self.create(f"lead_{i}", "crm.lead", {
                "name": PREFIX + f"Analysis enquiry {i + 1}", "partner_id": client.id,
                "type": "lead", "user_id": users["manager"].id,
            })
        self.create("fixture_version", "ir.config_parameter", {
            "key": "labtracker.test_data_version", "value": VERSION,
        })
        print("Created:", dict(Counter(m.model for m in self.markers() if m.res_id != 0)))
        return True

    def owned(self):
        result = {}
        for marker in self.markers():
            result.setdefault(marker.model, set()).add(marker.res_id)
        return result

    def guard_cleanup(self, owned):
        # Removing an owned parent can cascade-delete unowned children; removing
        # equipment/contacts/users can silently clear links. Refuse either case.
        targets = {
            "project.task": ["parent_id", "x_equipment_id", "partner_id", "user_ids", "depend_on_ids", "dependent_ids"],
            "project.project": ["partner_id", "user_id"],
            "maintenance.request": ["equipment_id", "user_id"],
            "maintenance.equipment": ["owner_user_id", "partner_id", "technician_user_id"],
            "crm.lead": ["partner_id", "user_id"],
            "res.partner": ["parent_id", "user_id"],
            "res.users": ["partner_id"],
        }
        for model, names in targets.items():
            records = self.env[model]
            for name in names:
                field = records._fields.get(name)
                ids = owned.get(field.comodel_name, set()) if field and field.relational else set()
                if not ids:
                    continue
                external = records.search([
                    ("id", "not in", list(owned.get(model, set()))), (name, "in", list(ids)),
                ], limit=1)
                if external:
                    raise UserError(f"Cleanup refused: non-test {model} #{external.id} references test data via {name}. "
                                    "Detach that link or remove that record deliberately, then retry. No changes committed.")

    def clean(self):
        owned = self.owned()
        if not owned:
            print("No owned test data found. Nothing to remove.")
            return
        self.guard_cleanup(owned)
        # Include chatter created while manually exercising these records.
        for model, ids in owned.items():
            for auxiliary, model_field, id_field in (
                ("mail.message", "model", "res_id"), ("mail.followers", "res_model", "res_id"),
                ("mail.activity", "res_model", "res_id"), ("ir.attachment", "res_model", "res_id"),
            ):
                self.env[auxiliary].search([(model_field, "=", model), (id_field, "in", list(ids))]).unlink()
        for model in ORDER:
            records = self.env[model].browse(list(owned.get(model, set()))).exists()
            if model == "project.task":
                # Delete samples first, avoiding task unlink prompts on parents.
                records.filtered("parent_id").unlink()
                records = records.exists()
            records.unlink()
        self.env["ir.config_parameter"].browse(list(owned.get("ir.config_parameter", set()))).exists().unlink()
        self.markers().unlink()
        print("Removed owned test records, users, contacts, chatter, activities, and attachments.")

    def verify_permissions(self):
        manager = self.record("user_manager")
        coordinator = self.record("user_coordinator")
        intern = self.record("user_intern")
        task = self.record("sample_0_0")
        eq = self.record("equipment_0")

        def as_user(record, user):
            return record.with_user(user).sudo(False)

        def denied(operation):
            try:
                with self.env.cr.savepoint():
                    operation()
                    # Roll back even an unexpectedly permitted write.
                    raise RuntimeError("permission unexpectedly granted")
            except (AccessError, UserError):
                return
            raise AssertionError("Expected access denial")

        for user in (manager, coordinator, intern):
            if as_user(user, user).has_group("base.group_system"):
                raise AssertionError("Test users must not have system administrator access")
        as_user(eq, manager).write({"note": "<p>Manager permission checked</p>"})
        denied(lambda: as_user(eq, coordinator).write({"note": "Should be denied"}))
        as_user(task, coordinator).write({"lab_time_taken": 0.5})
        as_user(task, intern).write({"x_scan": False})
        # 2026-11-22: the assignee guard is in the lab_dashboard model and raises a plain UserError (no ERROR log).
        denied(lambda: as_user(task, intern).write({"user_ids": [(6, 0, [])]}))
        # Interns open Lab Home limited to their own work, and may add samples to their own requests only.
        if self.env["lab.dashboard"].with_user(intern).sudo(False).get_data().get("scope") != "assigned":
            raise AssertionError("Intern Lab Home must be limited to assigned work")
        denied(lambda: as_user(task, intern).unlink())
        with self.env.cr.savepoint(flush=True) as sp:
            self.env["project.task"].with_user(intern).sudo(False).create({
                "name": PREFIX + "intern sample (rolled back)", "parent_id": task.parent_id.id,
                "project_id": task.project_id.id,
            })
            sp.rollback()
        denied(lambda: self.env["project.task"].with_user(intern).sudo(False).create({
            "name": PREFIX + "should not be created", "project_id": task.project_id.id,
            "user_ids": [(6, 0, intern.ids)],
        }))
        denied(lambda: self.env["project.task"].with_user(intern).sudo(False).create({
            "name": PREFIX + "should not be created", "parent_id": self.record("request_2").id,
            "project_id": task.project_id.id,
        }))
        with self.env.cr.savepoint(flush=True) as sp:  # coordinators may create/edit contacts, never delete them
            partner = self.env["res.partner"].with_user(coordinator).sudo(False).create({"name": PREFIX + "coordinator contact"})
            denied(lambda: partner.unlink())
            as_user(eq, coordinator).write({"x_condition": eq.x_condition or "available"})
            denied(lambda: as_user(eq, coordinator).write({"name": "Should be denied"}))
            # ---- equipment features (lab_dashboard 20.0.2.3.0)
            as_user(eq, coordinator).write({"x_condition": "under_repair", "lab_best_practices": "<p>Coordinator checked</p>",
                                            "lab_maintenance_procedures": "<p>Coordinator checked</p>"})
            denied(lambda: as_user(eq, coordinator).write({"serial_no": "Should be denied"}))
            denied(lambda: as_user(eq, intern).write({"lab_best_practices": "<p>Should be denied</p>"}))
            for xid in ("contact_type_sales", "contact_type_support", "contact_type_engineer"):
                if not self.env.ref(f"lab_dashboard.{xid}", raise_if_not_found=False):
                    raise AssertionError(f"Missing seeded contact type lab_dashboard.{xid}")
            typed = self.env["lab.equipment.contact.type"].with_user(coordinator).sudo(False).name_create(PREFIX + "Field engineer")[0]
            contact = self.env["lab.equipment.contact"].with_user(coordinator).sudo(False).create(
                {"equipment_id": eq.id, "partner_id": partner.id, "type_id": typed})
            denied(lambda: contact.unlink())
            denied(lambda: self.env["lab.equipment.contact.type"].with_user(coordinator).sudo(False).browse(typed).unlink())
            partner.write({"active": False})  # archive is allowed, delete is not
            # ---- end equipment features
            sp.rollback()
        denied(lambda: self.env["crm.lead"].with_user(coordinator).sudo(False).create({"name": PREFIX + "denied"}))
        other = self.record("request_2")
        if self.env["project.task"].with_user(intern).sudo(False).search_count([("id", "=", other.id)]):
            raise AssertionError("Intern can see another user's request")
        print("PASS: manager equipment write, coordinator equipment write limited to condition/best practices/procedures/contacts, "
              "free-text contact type, contact create/archive without delete, task edit, intern scope/checklist/assignee guard/own-sample create/own-work dashboard, no admin access.")

    def check_cleanup_safety(self, credentials):
        before = self.owned()
        if self.seed(credentials) or self.owned() != before:
            raise AssertionError("Repeat seed must not create or change records")
        print("PASS: repeat seed is a no-op")
        # A real child must never be cascade-deleted with a demo request.
        sentinel = self.env["project.task"].create({
            "name": "cleanup safety sentinel", "parent_id": self.record("request_0").id,
            "project_id": self.record("request_0").project_id.id,
        })
        try:
            self.clean()
        except UserError as error:
            if "Cleanup refused:" not in str(error):
                raise
        else:
            raise AssertionError("Cleanup must refuse an unowned dependent task")
        sentinel.unlink()
        # Names and archive state must not defeat ownership-based cleanup.
        self.record("sample_0_0").write({"name": "renamed fixture sample", "active": False})
        self.clean()
        for model, ids in before.items():
            if self.env[model].browse(list(ids)).exists():
                raise AssertionError(f"Cleanup left fixture rows in {model}")
        self.clean()
        print("PASS: refuses real dependents; removes renamed/archived fixture; repeat cleanup is a no-op")


def run(environment, options):
    # Serialize fixture creation/cleanup without preventing ordinary application use.
    environment.cr.execute("SELECT pg_advisory_xact_lock(194872, 1)")
    fixture = Fixture(environment)
    try:
        if options["check_cycle"] and fixture.markers():
            raise UserError("Clean the existing fixture before --check-cycle; it only validates a fresh fixture.")
        if options["action"] == "seed":
            created = fixture.seed(options["users"])
            if created:
                fixture.verify_permissions()
            if options["check_cycle"]:
                fixture.check_cleanup_safety(options["users"])
                if fixture.markers():
                    raise AssertionError("Cleanup left ownership markers")
                print("PASS: seed/permissions/cleanup cycle")
        else:
            fixture.clean()
        if options["dry_run"] or options["check_cycle"]:
            environment.cr.rollback()
            print("DRY RUN: all database changes rolled back.")
        else:
            environment.cr.commit()
            print("Committed successfully.")
    except BaseException:
        environment.cr.rollback()
        raise


run(env, TEST_OPTIONS)
