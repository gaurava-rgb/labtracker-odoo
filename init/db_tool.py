"""Postgres helper used by labtracker-entrypoint.sh (psycopg2 ships with the odoo image). Never prints any secret.

  python3 db_tool.py wait                 block until the primary accepts connections
  python3 db_tool.py exists               print "yes" if database $ODOO_DB exists, else "no"
  python3 db_tool.py roles                create/refresh roles replicator + dash_ro and the read-only grants on $ODOO_DB
  python3 db_tool.py module_version NAME  print the installed version of an Odoo module ("" if not installed)

Env: DB_HOST (db), DB_PORT (5432), DB_USER (odoo), POSTGRES_PASSWORD, ODOO_DB, REPLICATOR_PASSWORD, DASH_RO_PASSWORD
"""
import os
import sys
import time

import psycopg2
from psycopg2 import sql

HOST = os.environ.get("DB_HOST", "db")
PORT = int(os.environ.get("DB_PORT", "5432"))
USER = os.environ.get("DB_USER", "odoo")
PASSWORD = os.environ["POSTGRES_PASSWORD"]
DB = os.environ.get("ODOO_DB", "labtracker")


def connect(dbname="postgres"):
    conn = psycopg2.connect(host=HOST, port=PORT, user=USER, password=PASSWORD, dbname=dbname, connect_timeout=5)
    conn.autocommit = True
    return conn


def wait(timeout=300):
    deadline = time.time() + timeout
    while True:
        try:
            connect().close()
            return
        except psycopg2.OperationalError:
            if time.time() > deadline:
                raise SystemExit(f"database {HOST}:{PORT} not reachable after {timeout}s")
            time.sleep(2)


def exists():
    with connect() as c, c.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (DB,))
        return cur.fetchone() is not None


def ensure_role(cur, name, password, options):
    cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (name,))
    if not cur.fetchone():
        cur.execute(sql.SQL("CREATE ROLE {} LOGIN").format(sql.Identifier(name)))
    cur.execute(sql.SQL("ALTER ROLE {} WITH LOGIN " + options + " PASSWORD %s").format(sql.Identifier(name)), (password,))


def roles():
    rep_pw = os.environ.get("REPLICATOR_PASSWORD")
    ro_pw = os.environ.get("DASH_RO_PASSWORD")
    if not rep_pw or not ro_pw:
        raise SystemExit("REPLICATOR_PASSWORD and DASH_RO_PASSWORD must be set")
    with connect() as c, c.cursor() as cur:
        ensure_role(cur, "replicator", rep_pw, "REPLICATION NOSUPERUSER NOCREATEDB NOCREATEROLE")
        ensure_role(cur, "dash_ro", ro_pw, "NOREPLICATION NOSUPERUSER NOCREATEDB NOCREATEROLE")
        cur.execute("ALTER ROLE dash_ro SET default_transaction_read_only = on")
        cur.execute("ALTER ROLE dash_ro SET statement_timeout = '15s'")
    if not exists():
        print("roles ok; database not created yet, grants skipped")
        return
    with connect(DB) as c, c.cursor() as cur:
        cur.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO dash_ro").format(sql.Identifier(DB)))
        cur.execute("GRANT USAGE ON SCHEMA public TO dash_ro")
        cur.execute("GRANT SELECT ON ALL TABLES IN SCHEMA public TO dash_ro")
        cur.execute("GRANT SELECT ON ALL SEQUENCES IN SCHEMA public TO dash_ro")
        cur.execute(sql.SQL("ALTER DEFAULT PRIVILEGES FOR ROLE {} IN SCHEMA public GRANT SELECT ON TABLES TO dash_ro").format(sql.Identifier(USER)))
        cur.execute(sql.SQL("ALTER DEFAULT PRIVILEGES FOR ROLE {} IN SCHEMA public GRANT SELECT ON SEQUENCES TO dash_ro").format(sql.Identifier(USER)))
    print("roles ok; dash_ro grants applied on", DB)


def module_version(name):
    with connect(DB) as c, c.cursor() as cur:
        cur.execute("SELECT latest_version FROM ir_module_module WHERE name = %s AND state = 'installed'", (name,))
        row = cur.fetchone()
        return (row[0] or "") if row else ""


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "wait":
        wait()
    elif cmd == "exists":
        print("yes" if exists() else "no")
    elif cmd == "roles":
        roles()
    elif cmd == "module_version":
        print(module_version(sys.argv[2]))
    else:
        raise SystemExit(__doc__)
