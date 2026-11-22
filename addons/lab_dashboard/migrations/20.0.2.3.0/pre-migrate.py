"""20.0.2.3.0: lab.equipment.contact.role (fixed selection) becomes type_id (Many2one to lab.equipment.contact.type).

Before the registry loads the new model, park the old values in a side column so they survive the removal of the
`role` field (Odoo drops columns of removed fields at the end of the upgrade). post-migrate.py reads them back.
"""


def migrate(cr, version):
    cr.execute("""SELECT 1 FROM information_schema.columns
                   WHERE table_name = 'lab_equipment_contact' AND column_name = 'role'""")
    if cr.fetchone():
        cr.execute("ALTER TABLE lab_equipment_contact RENAME COLUMN role TO lab_legacy_role")
