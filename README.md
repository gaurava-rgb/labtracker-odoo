# Lab Tracker on Odoo: deploy in 3 commands

Lab Tracker is a ready-made Odoo 20 setup for a small lab: requests and samples (Project), instruments
(Maintenance), clients (Contacts, CRM), three staff roles, and a "Lab Home" dashboard that reads from a
live read-only copy of the database. Everything is set up automatically the first time it starts.

First time doing this? Read **[DEPLOY_GUIDE.md](DEPLOY_GUIDE.md)**: a step-by-step walkthrough from renting a server to adding users.

## What you need

- A Linux server (VM) with Docker and Docker Compose installed. Ubuntu 22.04 or 24.04 works well.
  Test: `docker compose version` prints a version.
- At least 2 GB of RAM and 10 GB of free disk. 4 GB RAM is more comfortable.
- A domain name (for example `lab.example.com`) whose DNS A record points at the server's public IP.
  Ports 80 and 443 must be open and not used by anything else.
- This folder copied to the server (for example into `/opt/labtracker`).
- The Docker image. Either build it on the server (`docker build -t labtracker-odoo:20.0 .`, takes about a
  minute after the 2.3 GB base image has downloaded) or load a copy someone sent you
  (`docker load -i labtracker-odoo-20.0.tar`). Nothing is pulled from a public registry.

## The 3 commands

```bash
./make-env.sh                                  # 1. creates .env with random passwords (once)
nano .env                                      # 2. set DOMAIN and COMPANY_NAME, save (Ctrl+O, Enter, Ctrl+X)
docker compose --profile tls --profile backup up -d   # 3. start everything
```

First start takes 3 to 5 minutes: it creates the database, installs the apps, applies the Lab setup and
requests the HTTPS certificate. Watch progress with `docker compose logs -f odoo` (lines starting with
`[labtracker]`; the last one is `provisioning finished`). Press Ctrl+C to stop watching.

No domain yet? Skip the `--profile tls` part, set `ODOO_BIND=0.0.0.0` in `.env`, and open
`http://SERVER-IP:8069` instead. Plain http is fine for a trial on a private network only.

## First login

1. Open `https://YOUR-DOMAIN` in a browser.
2. Email: `admin`. Password: the `ADMIN_PASSWORD` line in `.env` (show it with `grep ^ADMIN_PASSWORD .env`).
3. You land in Odoo. Click the **Lab** app (top-left menu) to see Lab Home, or **Project > Lab requests**.

Change the admin password afterwards: click the user name (top right) > Preferences > Account Security.

## Add people and roles

Settings > Users & Companies > Users > New. Fill name and email, then under **Lab** pick one role:

| Role | Can do |
|------|--------|
| Manager | Everything in Project, Maintenance, Contacts and CRM. Sees Lab Home. |
| Coordinator | Creates and edits requests and samples. Read-only on equipment, contacts, CRM. Sees Lab Home. |
| Intern | Sees and edits only the requests and samples assigned to them. No Lab Home, no CRM. |

Click **Send an Invitation Email** (if outgoing mail is configured) or set a password under Actions > Change Password.
Leave the other access boxes as Odoo sets them; the Lab role already includes what each role needs.

Want three ready-made test users (`manager@lab.test`, `coordinator@lab.test`, `intern@lab.test`)? Set
`LAB_CREATE_TEST_USERS=1` and the three `LAB_TEST_*_PW` lines in `.env`, then `docker compose up -d odoo`.

## Day-to-day checks

```bash
docker compose ps                 # all services "Up" / "healthy"
./scripts/verify.sh               # 9 checks: apps, Lab rules, roles, fields, dashboard, LAB- numbering
./scripts/check-replica.sh        # the read-only copy is streaming (want 1 / t / replica)
docker compose logs --tail 50 odoo
```

**The replica.** `db` is the main database. `db_replica` keeps a live copy a few milliseconds behind and
nobody can write to it. Lab Home reads its numbers from that copy (blue "replica" badge). If the copy is
down, Lab Home silently reads from the main database instead (yellow "live data" badge) and nothing else
changes. To rebuild the copy from scratch: `./scripts/rebuild-replica.sh`.

## Backups

- Every night at 03:10 (server time, change `BACKUP_TIME` in `.env`) the `backup` service writes two files
  into `./backups/`: `labtracker-YYYY-MM-DD.dump` (the database) and `filestore-YYYY-MM-DD.tgz` (uploaded files).
- Files older than 14 days are deleted (`BACKUP_KEEP_DAYS`).
- Take one right now: `./scripts/backup-now.sh`.
- Copy `./backups/` somewhere off the server regularly (another machine, object storage). A backup that only
  lives on the same disk is not a backup.

## Restore from a backup

```bash
./scripts/restore.sh backups/labtracker-2026-10-25.dump
```

It asks you to type `yes`, stops Odoo, replaces the database with the dump, puts the matching filestore
back if `filestore-2026-10-25.tgz` exists, rebuilds the read-only copy and starts Odoo again. About 2 minutes.

## Update to a new version

```bash
docker build -t labtracker-odoo:20.0 .        # or: docker load -i labtracker-odoo-20.0.tar
docker compose --profile tls --profile backup up -d
```

Only the `odoo` container restarts. On start it upgrades the Lab Home add-on if the image carries a newer
one and re-applies the Lab setup if that changed. Your data stays in the Docker volumes
(`pgdata`, `pgdata_replica`, `odoo_data`, `caddy_data`). Take a backup first (`./scripts/backup-now.sh`).

## Optional: load the demo data

The demo set (clients, instruments, requests, samples) lives in a separate Postgres database and is **not**
in the image. From any computer that can reach both:

```bash
pip install "psycopg[binary]"
LAB_DATABASE_URL='postgres://...'  ODOO_URL=https://YOUR-DOMAIN  ODOO_DB=labtracker  ODOO_PASSWORD='the admin password' \
  python3 tools/load_demo_data.py
```

Re-running it updates instead of duplicating. The LAB- numbering continues after the highest imported code.

## Stop, start, remove

```bash
docker compose stop                     # pause everything, keeps data
docker compose --profile tls --profile backup up -d   # start again
docker compose down                     # remove containers, keeps data volumes
docker compose down -v                  # remove containers AND all data (backups/ folder stays)
```

## Files in this folder

| Path | What it is |
|------|------------|
| `Dockerfile`, `labtracker-entrypoint.sh` | The custom image: Odoo 20 + add-on + self-provisioning start script |
| `init/` | Provisioning scripts run inside the container (`provision.py` drives `lab_rules.py`, `lab_phase2.py`, `lab_roles.py`) |
| `addons/lab_dashboard/` | The Lab Home dashboard add-on |
| `docker-compose.yml` | The 5 services: `db`, `db_replica`, `odoo`, `caddy` (profile tls), `backup` (profile backup) |
| `db/` | Primary init SQL (roles, replication access) and the standby start script |
| `caddy/Caddyfile` | HTTPS front door |
| `backup/backup.sh` | Nightly backup loop |
| `scripts/` | `verify.sh`, `check-replica.sh`, `rebuild-replica.sh`, `backup-now.sh`, `restore.sh` |
| `tools/load_demo_data.py` | Optional demo-data loader (runs outside the image) |
| `make-env.sh`, `.env.example` | Create the secrets file |
| `ARCHITECTURE.md` | Diagram of how the pieces connect |

## Troubleshooting

- `docker compose logs odoo` shows `provisioning FAILED`: fix the cause shown above it (usually the database
  was still starting) and run `docker compose restart odoo`. Provisioning retries on every start.
- `https://YOUR-DOMAIN` shows a certificate error: check that DNS points at this server and ports 80/443 are
  open, then `docker compose logs caddy`.
- Lab Home shows "live data": run `./scripts/check-replica.sh`; if it is not streaming, `./scripts/rebuild-replica.sh`.
- Forgot the admin password: it is the `ADMIN_PASSWORD` line in `.env` unless it was changed inside Odoo. If
  changed, reset it with
  `docker compose exec odoo odoo shell -c /etc/odoo/odoo.conf -d labtracker --no-http` and
  `env['res.users'].browse(2).write({'password': 'NEW'}); env.cr.commit()`.
