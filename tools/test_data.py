"""Host-side runner. Streams the ORM script into the existing image; no rebuild needed."""
import argparse
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
CREDENTIALS = ROOT / ".test-data-credentials.json"
ROLES = ("manager", "coordinator", "intern")


def credentials():
    if CREDENTIALS.exists():
        if CREDENTIALS.is_symlink() or not CREDENTIALS.is_file() or CREDENTIALS.stat().st_mode & 0o077:
            raise SystemExit("Credentials must be a regular, private file (chmod 600).")
        data = json.loads(CREDENTIALS.read_text())
    else:
        data = {role: {"login": f"demo.{role}@lab.invalid", "password": secrets.token_urlsafe(24)}
                for role in ROLES}
        fd = os.open(CREDENTIALS, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as file:
            json.dump(data, file, indent=2)
            file.write("\n")
    if set(data) != set(ROLES) or any(
        data[r].get("login") != f"demo.{r}@lab.invalid" or not data[r].get("password") for r in ROLES
    ):
        raise SystemExit("Unexpected test credentials format; refusing to continue.")
    return data


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("seed", "clean"))
    parser.add_argument("--dry-run", action="store_true", help="Perform checks, then roll back all database changes")
    parser.add_argument("--check-cycle", action="store_true", help="Seed, check permissions, clean, then roll back (seed only)")
    args = parser.parse_args()
    if args.check_cycle and args.action != "seed":
        parser.error("--check-cycle is only supported by seed")
    db = "labtracker"
    for line in (ROOT / ".env").read_text().splitlines():
        if line.startswith("ODOO_DB="):
            db = line.split("=", 1)[1].strip().strip('\"\'') or db
    if args.action == "seed":
        if args.dry_run or args.check_cycle:
            users = {r: {"login": f"demo.{r}@lab.invalid", "password": secrets.token_urlsafe(24)} for r in ROLES}
        else:
            users = credentials()
    else:
        users = {}
    options = {"action": args.action, "dry_run": args.dry_run, "check_cycle": args.check_cycle, "users": users}
    source = "TEST_OPTIONS = " + repr(options) + "\n" + (ROOT / "tools/test_data_orm.py").read_text()
    result = subprocess.run(
        ["docker", "compose", "exec", "-T", "odoo", "odoo", "shell", "-c", "/etc/odoo/odoo.conf",
         "-d", db, "--no-http", "--log-level=error"],
        input=source, text=True, cwd=ROOT,
    )
    if result.returncode:
        raise SystemExit(result.returncode)
    if not (args.dry_run or args.check_cycle):
        if args.action == "seed":
            print(f"Private test logins/passwords: {CREDENTIALS}\nView locally with: cat .test-data-credentials.json")
        else:
            CREDENTIALS.unlink(missing_ok=True)
            print("Removed local test credentials file.")


if __name__ == "__main__":
    main()
