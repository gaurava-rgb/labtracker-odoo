# Lab Tracker: test deployment on your existing Hetzner VPS

Use this guide instead of `DEPLOY_GUIDE.md` for this server. It starts from the cleanup completed on November 11, 2026, while preserving the email tracker and Hermes files.

## 1. Understand what stays on the server

At the time of cleanup:

- Server: SSH alias `hetzner`, public IPv4 `89.167.14.119`.
- Ubuntu 24.04, 4 GB RAM, about 21 GB disk space available before reinstalling Docker.
- Email tracker: `/opt/mailtrack`, running through nginx at `t.aididmywork.com`.
- Hermes: `/usr/local/lib/hermes-agent` and `/root/.hermes`, preserved but stopped and disabled.
- Docker, its previous containers and volumes, the old Lab Tracker, and the failed Odoo checkout were removed.
- Hetzner firewall `firewall-2`: inbound TCP 22, 80, 443 and ICMP allowed for IPv4/IPv6; other inbound traffic blocked; outbound unrestricted.

**Do not rebuild the VPS.** A rebuild erases the email tracker, Hermes, and the rest of the server. We are testing a fresh Lab Tracker deployment on an existing host, not testing a completely fresh Ubuntu installation.

**Do not stop or uninstall nginx.** It serves the email tracker and will also serve Lab Tracker under a separate domain.

Nothing in this guide requires starting Hermes. Leave it stopped while testing.

## 2. Connect and check the starting state

On your laptop:

```sh
ssh hetzner
```

Everything below runs on the server as root unless explicitly marked otherwise.

```sh
hostname
systemctl is-active mailtrack nginx
dpkg --audit
df -h /
```

Expected: both services say `active`, `dpkg --audit` prints nothing, and there is adequate disk space. Stop if these checks fail. A package error should be resolved before installing more software.

## 3. Choose the Lab Tracker domain

Use a domain you control, for example `lab.aididmywork.com`. The old Lab Tracker's nginx entry for that domain was removed during cleanup, so it can be reused if you still want that address.

**Do not use `t.aididmywork.com`.** That address belongs to the email tracker; reusing it would replace its nginx configuration and interrupt email tracking.

In your DNS provider, make the chosen domain's `A` record point to `89.167.14.119`. Leave the email tracker's DNS record alone. If you have an `AAAA` record, make sure it points to this server's actual IPv6 address; otherwise remove that domain's stale `AAAA` record. For Cloudflare, use DNS-only mode during this test to keep certificate troubleshooting straightforward.

On the server, set your chosen domain once for use by later commands:

```sh
LAB_DOMAIN=lab.aididmywork.com
getent ahostsv4 "$LAB_DOMAIN"
```

Replace the example if needed. Expected: the server's IPv4 address appears. If you reconnect later, set `LAB_DOMAIN` again; shell variables do not survive logout.

Check whether nginx already has a configuration for this domain:

```sh
ls -l "/etc/nginx/sites-available/$LAB_DOMAIN" "/etc/nginx/sites-enabled/$LAB_DOMAIN"
```

On the first deployment after cleanup, “No such file or directory” is expected. If a file already exists, inspect it before continuing: `scripts/nginx-site.sh` writes that domain's file and can overwrite an existing site's settings.

## 4. Install Docker using Ubuntu's packages

For this test, use Ubuntu's `docker.io` and `docker-compose-v2` packages together:

```sh
apt-get update
apt-get install -y ca-certificates git curl nano docker.io docker-compose-v2
systemctl enable --now docker
docker compose version
docker info
```

Expected: Compose reports a version, and `docker info` includes a **Server** section with no connection error. **Do not continue until both succeed.** A Compose version alone does not show whether the engine works.

**Do not also run the Docker installer from the original guide.** It installs Docker CE from Docker's repository. This guide deliberately uses Ubuntu's package family; switching families mid-install caused the earlier interrupted package installation and broken socket on this VPS.

If startup fails, collect:

```sh
systemctl status docker.service docker.socket --no-pager -l
journalctl -u docker.service -u docker.socket -b -n 100 --no-pager -o cat
```

If the actual error specifically says `no sockets found via socket activation`, run:

```sh
systemctl stop docker.service docker.socket
systemctl reset-failed docker.service docker.socket
systemctl start docker.socket
systemctl start docker.service
docker info
```

For a different error, stop and diagnose it using the logs. Do not delete Docker data or repeatedly reinstall packages as a troubleshooting shortcut.

## 5. Get the application

```sh
git clone https://github.com/gaurava-rgb/labtracker-odoo /opt/labtracker
cd /opt/labtracker
```

If the directory already exists, stop and inspect it. Do not delete it just to make the clone command succeed; it may contain a previous attempt's secrets or backups.

The guide you are reading may only exist on your laptop until it is committed and pushed. Keep this copy open while running the commands over SSH.

## 6. Create the secrets file

```sh
./make-env.sh
nano .env
```

Set these two values, using the same domain selected in step 3:

```dotenv
DOMAIN=lab.aididmywork.com
COMPANY_NAME=Your Lab Name
```

Save with Ctrl+O, Enter, then Ctrl+X.

Leave `ODOO_BIND=127.0.0.1` unchanged. Nginx reaches Odoo locally; visitors reach nginx over HTTPS. There is no need to expose Odoo or PostgreSQL directly or open ports 8069, 8072, or 5432 in the firewall.

**Do not follow the startup command printed by `make-env.sh`.** Its generic message includes `--profile tls`, which starts Caddy on ports 80/443. Nginx already owns those ports here. Use step 7 below instead.

**Do not delete and recreate `.env` on later retries.** Its passwords must continue matching the database. The script refuses to overwrite an existing file for this reason. Never share or commit `.env`.

## 7. Start Odoo, its databases, and backups

```sh
docker compose --profile backup config --quiet
docker compose --profile backup up -d
docker compose logs -f odoo
```

The first command validates configuration without printing the resolved secrets. The first startup downloads images and initializes the application; allow several minutes. Wait for `provisioning finished`, then press Ctrl+C to stop watching. That does not stop the application.

**Do not add `--profile tls` anywhere in this guide.** That profile starts Caddy and conflicts with nginx. This deployment gets HTTPS through nginx instead.

If image downloads or provisioning fail, keep `.env` and the volumes, collect the error, and troubleshoot. Retrying `up -d` is normally enough after fixing the underlying problem.

## 8. Add the Lab Tracker nginx site and HTTPS

Before running the script, confirm `$LAB_DOMAIN` still matches `DOMAIN` in `.env`:

```sh
printf 'Chosen domain: %s\n' "$LAB_DOMAIN"
grep '^DOMAIN=' .env
nginx -t
scripts/nginx-site.sh "$LAB_DOMAIN"
```

The script adds a site for that domain, proxies it to local Odoo ports 8069/8072, obtains a Let's Encrypt certificate, and reloads nginx. It also accepts Let's Encrypt's subscriber terms through Certbot. Read those terms first if you need to review them.

Expected: the script finishes with a `Done: https://...` message. DNS must point here and ports 80/443 must be reachable for certificate issuance. The existing Hetzner firewall already allows them.

**Do not replace nginx's main configuration or remove the email tracker's site.** Multiple domains can share nginx on the same ports; each domain routes to its own application.

## 9. Verify the deployment and preserved services

```sh
docker compose --profile backup ps
scripts/verify.sh
scripts/check-replica.sh
systemctl is-active mailtrack nginx
nginx -t
curl -sS -o /dev/null -w 'Email tracker HTTP status: %{http_code}\n' https://t.aididmywork.com/
```

Expected:

- The four services `db`, `db_replica`, `odoo`, and `backup` are running; the database containers become healthy. There should be no Caddy container.
- The verification script succeeds. The replica check reports one streaming standby and `t` for recovery mode.
- Mailtrack and nginx remain `active`, and nginx's configuration test passes.
- The email tracker's root URL returns **404**. This is expected: the app serves tracking-pixel URLs, not a home page. This checks HTTPS reachability without creating a fake email-open record.

Open `https://lab.aididmywork.com` (or your chosen domain) on your laptop. Show the initial password privately on the server:

```sh
grep '^ADMIN_PASSWORD=' .env
```

Log in as `admin`. Change the password through Preferences → Account Security → Change password. Do not paste it into a support chat.

## 10. Stop, resume, or repeat your test

Run these from `/opt/labtracker`:

| Goal | Command | What it does |
|---|---|---|
| Watch startup | `docker compose logs -f odoo` | Shows logs; Ctrl+C stops watching only |
| Stop the test | `docker compose --profile backup stop` | Stops only this Compose project's containers; keeps its data |
| Resume | `docker compose --profile backup up -d` | Starts the stack using existing data |
| Restart | `docker compose --profile backup restart` | Restarts containers without resetting the database |
| Remove containers, keep data | `docker compose --profile backup down` | Removes this project's containers/network; named volumes remain |
| Back up | `scripts/backup-now.sh` | Creates an application backup before changes |

**Do not stop the global Docker service to stop this test.** Compose commands target the Lab Tracker project; stopping Docker affects every container on the host.

**Do not use `down -v` for a normal retry.** It permanently deletes this project's database and Odoo attachment volumes. Only use it if you deliberately want to discard all test data and have verified that nothing needs saving. You do not need it to verify startup or restart behavior.

Stopping Odoo can make its domain return a gateway error while the test is stopped. The email tracker should keep working because it is a separate service and nginx site.

This tests a new Lab Tracker deployment alongside an existing nginx service. It does not prove every step of the separate fresh-VPS guide works on a blank machine.
