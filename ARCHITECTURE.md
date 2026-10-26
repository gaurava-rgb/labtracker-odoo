# Architecture

Five containers on one Docker Compose network. Only `caddy` (ports 80/443) is reachable from the internet;
`odoo` is bound to 127.0.0.1 on the host and the databases have no host ports at all.

```mermaid
flowchart LR
    user([Browser]) -- "https://DOMAIN" --> caddy

    subgraph host["Linux server (docker compose)"]
        caddy["caddy (profile tls)\nLet's Encrypt, reverse proxy"]
        odoo["odoo\nlabtracker-odoo:20.0\nentrypoint: create db, install apps,\nprovision Lab setup, serve"]
        db[("db\npostgres:16 primary\nroles: odoo, replicator, dash_ro")]
        replica[("db_replica\npostgres:16 hot standby\nread-only copy")]
        backup["backup (profile backup)\npg_dump + filestore tar\nnightly, 14 days"]

        caddy -- "8069 http / 8072 websocket" --> odoo
        odoo -- "read + write (user odoo)" --> db
        db -- "streaming replication\n(user replicator, slot labtracker_replica)" --> replica
        odoo -. "Lab Home dashboard SQL\n(user dash_ro, LAB_DASH_DSN)" .-> replica
        backup -- "pg_dump (user odoo)" --> db
    end

    env[".env\nall passwords"] -.-> odoo & db & replica & backup & caddy
    vol_pg[["volume pgdata"]] --- db
    vol_rep[["volume pgdata_replica"]] --- replica
    vol_fs[["volume odoo_data\n(filestore)"]] --- odoo
    vol_fs -. read-only .- backup
    dir_bk[["./backups/ on the host"]] --- backup
```

## Boot sequence of the `odoo` container

```mermaid
sequenceDiagram
    participant E as labtracker-entrypoint.sh
    participant P as Postgres (db)
    participant O as Odoo server
    participant V as init/provision.py
    E->>E: write /etc/odoo/odoo.conf from env
    E->>P: wait, create/refresh roles replicator + dash_ro, grants
    alt database missing
        E->>P: odoo -i base,contacts,project,crm,maintenance,base_automation,lab_dashboard --without-demo
        E->>P: odoo shell: set admin password + company name
    else image carries a newer lab_dashboard
        E->>P: odoo -u lab_dashboard
    end
    E->>O: exec odoo (foreground)
    E-->>V: background
    V->>O: wait for /web/health
    V->>O: read ir.config_parameter labtracker.provisioned_version
    alt version differs or FORCE_PROVISION=1
        V->>O: lab_rules (project, LAB- sequence, x_equipment_id, x_condition, views, filters)
        V->>O: lab_phase2 (sample/request fields, rules, stage cleanup)
        V->>O: lab_roles (Lab privilege, Manager/Coordinator/Intern, access rows, menus)
        V->>O: write marker parameter
    else
        V->>V: nothing to do
    end
```

## What lives where

| Thing | Location | Created by |
|-------|----------|-----------|
| Odoo database `labtracker` | volume `pgdata` | entrypoint on first boot |
| Read-only copy | volume `pgdata_replica` | `db/replica-entrypoint.sh` via `pg_basebackup -R` on first boot |
| Uploaded files (filestore) | volume `odoo_data` | Odoo |
| TLS certificates | volume `caddy_data` | Caddy |
| Backups | `./backups/` (host folder) | `backup/backup.sh` |
| Secrets | `./.env` (mode 600) | `make-env.sh` |
| Lab customisations (fields, rules, views, roles) | inside the Odoo database | `init/*.py`, idempotent, marker `labtracker.provisioned_version` |

## Security notes

- Database users: `odoo` (owner, used by Odoo and backups), `replicator` (replication only, may connect
  only from the compose network), `dash_ro` (SELECT only, read-only transactions, 15 s statement timeout).
- Odoo runs with `list_db = False` and a `dbfilter`, so the database manager is hidden; the master
  password (`ODOO_MASTER_PASSWORD`) is still required for the few manager endpoints that remain.
- Lab Home access is enforced server-side (`lab.dashboard.get_data` raises AccessError for interns), the
  menu visibility set by `lab_roles.py` is only cosmetic.
- Interns are limited by a global `ir.access` restriction on `project.task` (Odoo 20 merged record rules
  and access rights into `ir.access`), plus an automation that blocks assignee changes.
