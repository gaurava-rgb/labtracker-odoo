"""20.0.2.2.0: add the "Pending pickup" stage after Complete (and unfold Complete), keep stage/state consistent,
and copy the old free-text x_time_spent values into the numeric lab_time_taken field.
Runs during `odoo -u lab_dashboard`, before provisioning. Both steps are idempotent."""
from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    tasks = env["project.task"]
    tasks._lab_setup_workflow()
    tasks._lab_migrate_time_spent()
