"""20.0.2.3.0: fill lab.equipment.contact.type_id from the old selection value parked by pre-migrate.py.

Old value          -> contact type (record)
  sales            -> Sales               (seed lab_dashboard.contact_type_sales)
  support          -> Support             (seed lab_dashboard.contact_type_support)
  service_machine  -> Machines / service  (created if missing; same label the old selection showed)
  other            -> Other               (created if missing)
Rows that already have a type are left alone. The side column is dropped afterwards. Idempotent.
"""
from odoo import SUPERUSER_ID, api

LEGACY = {
    "sales": ("lab_dashboard.contact_type_sales", "Sales"),
    "support": ("lab_dashboard.contact_type_support", "Support"),
    "service_machine": (None, "Machines / service"),
    "other": (None, "Other"),
}


def _type_id(env, xmlid, name):
    rec = env.ref(xmlid, raise_if_not_found=False) if xmlid else None
    if not rec:
        Type = env["lab.equipment.contact.type"].with_context(active_test=False)
        rec = Type.search([("name", "=", name)], limit=1) or Type.create({"name": name})
    return rec.id


def migrate(cr, version):
    cr.execute("""SELECT 1 FROM information_schema.columns
                   WHERE table_name = 'lab_equipment_contact' AND column_name = 'lab_legacy_role'""")
    if not cr.fetchone():
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    cr.execute("SELECT DISTINCT lab_legacy_role FROM lab_equipment_contact WHERE lab_legacy_role IS NOT NULL")
    for (value,) in cr.fetchall():
        xmlid, name = LEGACY.get(value, (None, value))  # unknown value: keep it as its own free-text type
        cr.execute("UPDATE lab_equipment_contact SET type_id = %s WHERE lab_legacy_role = %s AND type_id IS NULL",
                   (_type_id(env, xmlid, name), value))
    cr.execute("ALTER TABLE lab_equipment_contact DROP COLUMN lab_legacy_role")
    env.invalidate_all()
