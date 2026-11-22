{
    "name": "Lab Dashboard",
    "summary": "Lab Home: live tiles and lists for lab requests and equipment, read from the Postgres replica",
    "version": "20.0.2.3.0",
    "category": "Services/Project",
    "author": "Lab Tracker",
    "license": "LGPL-3",
    "depends": ["project", "maintenance", "web"],
    "data": ["security/ir.access.csv", "views/menu.xml", "views/workflow.xml", "views/equipment_contacts.xml",
             # equipment features 20.0.2.3.0
             "data/contact_types.xml", "views/equipment_practices.xml"],
    "assets": {"web.assets_backend": ["lab_dashboard/static/src/**/*"]},
    "installable": True,
    "application": False,
}
