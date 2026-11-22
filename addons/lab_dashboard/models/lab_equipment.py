"""Equipment: contact persons per instrument (with a free-text contact type), best-practice / maintenance-procedure
pages, and the Coordinator write guard.

Coordinators get a write permission on maintenance.equipment from init/lab_roles.py (ir.access rows), so they can
change the instrument condition. This module is the real guard: a coordinator write may only touch COORDINATOR_FIELDS
(condition, the Best practices and Maintenance procedures pages, the Contacts tab whose own access rows apply) and
mail/activity bookkeeping keys. The form view also shows every other field read-only to coordinators, but that is
only a convenience.
"""
from odoo import api, fields, models
from odoo.exceptions import AccessError

COORDINATOR = "lab_tracker.group_lab_coordinator"
UNRESTRICTED = ("lab_tracker.group_lab_manager", "maintenance.group_equipment_manager", "base.group_system")
COORDINATOR_FIELDS = {"x_condition", "lab_contact_ids", "lab_best_practices", "lab_maintenance_procedures"}
HARMLESS_PREFIXES = ("message_", "activity_")


class LabEquipmentContactType(models.Model):
    """Free-text kind of equipment contact (Sales, Support, Engineer, ...). Picked or typed (quick-create) on a contact."""
    _name = "lab.equipment.contact.type"
    _description = "Equipment contact type"
    _order = "sequence, name, id"

    name = fields.Char(string="Type", required=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)

    _name_uniq = models.Constraint("unique(name)", "This contact type already exists.")


class LabEquipmentContact(models.Model):
    _name = "lab.equipment.contact"
    _description = "Equipment contact person"
    _order = "equipment_id, sequence, id"

    sequence = fields.Integer(default=10)
    equipment_id = fields.Many2one("maintenance.equipment", string="Equipment", required=True, ondelete="cascade", index=True)
    partner_id = fields.Many2one("res.partner", string="Contact", required=True, ondelete="restrict", index=True)
    # was a fixed selection `role` up to 20.0.2.1.1; migrations/20.0.2.3.0 moves the old values here
    type_id = fields.Many2one("lab.equipment.contact.type", string="Type", ondelete="restrict", index=True)
    note = fields.Char(string="Note")
    active = fields.Boolean(default=True)
    phone = fields.Char(related="partner_id.phone", string="Phone", readonly=True)
    email = fields.Char(related="partner_id.email", string="Email", readonly=True)
    company_id = fields.Many2one(related="partner_id.commercial_partner_id", string="Company", readonly=True)

    @api.depends("partner_id", "type_id")
    def _compute_display_name(self):
        for rec in self:
            name = rec.partner_id.display_name or ""
            rec.display_name = f"{name} ({rec.type_id.name})" if rec.type_id else name


class MaintenanceEquipment(models.Model):
    _inherit = "maintenance.equipment"

    lab_contact_ids = fields.One2many("lab.equipment.contact", "equipment_id", string="Contacts")
    lab_contact_count = fields.Integer(string="Contact count", compute="_compute_lab_contact_count")
    lab_best_practices = fields.Html(string="Best practices")
    lab_maintenance_procedures = fields.Html(string="Maintenance procedures")

    @api.depends("lab_contact_ids")
    def _compute_lab_contact_count(self):
        for rec in self:
            rec.lab_contact_count = len(rec.lab_contact_ids)

    def action_lab_contacts(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": self.env._("Contacts"),
            "res_model": "lab.equipment.contact",
            "view_mode": "list,form",
            "domain": [("equipment_id", "=", self.id)],
            "context": {"default_equipment_id": self.id},
        }

    # ------------------------------------------------------------ coordinator: condition, practices, contacts only
    def _lab_in_group(self, xmlid):
        group = self.env.ref(xmlid, raise_if_not_found=False)
        return bool(group) and self.env.user in group.all_user_ids

    def _lab_condition_only(self):
        """True for a Lab Coordinator who is not also a Manager / equipment manager / system admin.
        (Name kept from when only the condition was writable; the allowed set is COORDINATOR_FIELDS.)"""
        if self.env.su or not self._lab_in_group(COORDINATOR):
            return False
        return not any(self._lab_in_group(x) for x in UNRESTRICTED)

    def write(self, vals):
        if self._lab_condition_only():
            extra = sorted(k for k in vals if k not in COORDINATOR_FIELDS and not k.startswith(HARMLESS_PREFIXES))
            if extra:
                raise AccessError(self.env._(
                    "Lab coordinators are not allowed to change equipment fields other than Condition, Best practices, "
                    "Maintenance procedures and Contacts (attempted: %s).",
                    ", ".join(extra)))
        return super().write(vals)

    @api.model
    def _get_view_cache_key(self, view_id=None, view_type="form", **options):
        key = super()._get_view_cache_key(view_id, view_type, **options)
        return key + (("lab_condition_only", view_type == "form" and self._lab_condition_only()),)

    @api.model
    def _get_view(self, view_id=None, view_type="form", **options):
        arch, view = super()._get_view(view_id, view_type, **options)
        if view_type == "form" and self._lab_condition_only():
            for node in arch.iter("field"):
                # only fields of the equipment itself, not the columns of the Contacts list
                if node.get("name") in COORDINATOR_FIELDS or any(p.tag in ("list", "kanban", "form") and p is not arch
                                                                  for p in node.iterancestors()):
                    continue
                node.set("readonly", "1")
        return arch, view
