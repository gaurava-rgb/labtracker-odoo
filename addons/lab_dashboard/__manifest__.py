{
    "name": "Lab Dashboard",
    "summary": "Lab Home: live tiles and lists for lab requests and equipment, read from the Postgres replica",
    "version": "20.0.1.0.0",
    "category": "Services/Project",
    "author": "Lab Tracker",
    "license": "LGPL-3",
    "depends": ["project", "maintenance", "web"],
    "data": ["views/menu.xml"],
    "assets": {"web.assets_backend": ["lab_dashboard/static/src/**/*"]},
    "installable": True,
    "application": False,
}
