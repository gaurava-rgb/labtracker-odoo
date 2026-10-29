# Lab Tracker: deployment guide for first-timers

This walks you from "I have nothing" to "the lab is using it", in order. Every step says what you should see. If you see something else, stop and check the Troubleshooting section at the end.

Total time: about 45 minutes, most of it waiting.

---

## 0. What you are setting up

- One small Linux server (a "VPS") rented from any provider.
- A web address (a "domain") that points at that server.
- The Lab Tracker, which runs inside Docker on that server. Docker is a tool that runs programs in sealed boxes so they do not interfere with anything else.

You do not need to know Odoo, Postgres, or Docker. You will copy and paste about ten commands.

---

## 1. Rent a server (10 minutes)

Any provider works: Hetzner, DigitalOcean, Linode, Vultr, OVH. Pick:

- **Operating system:** Ubuntu 24.04 (plain, not a "Docker" image or any app image).
- **Size:** 2 vCPU, 4 GB RAM, 40 GB disk. The smallest that works is 2 GB RAM, but 4 GB is comfortable. About 5 to 8 USD a month.
- **Location:** close to the lab.
- **SSH key:** if the provider offers to add your SSH key, do it. Otherwise it will email you a root password.

Write down the server's **public IP address** (four numbers like `203.0.113.10`).

---

## 2. Point your domain at it (5 minutes, then wait)

In your DNS provider (Cloudflare, Namecheap, GoDaddy, Google Domains, wherever you bought the domain):

1. Add a record: **Type** `A`, **Name** the sub-name you want (for example `lab`, which gives `lab.yourdomain.com`), **Value** the server IP.
2. If you use **Cloudflare**, set the cloud icon to **DNS only (grey)** for this record. Orange (proxied) breaks the automatic certificate the first time. You can turn it orange later.
3. Wait 5 to 30 minutes.

Check from your laptop:

```sh
ping -c 1 lab.yourdomain.com
```

You should see your server's IP in the reply. If not, wait longer.

---

## 3. Log in to the server (2 minutes)

On your laptop, open Terminal (Mac) or PowerShell (Windows) and run:

```sh
ssh root@203.0.113.10
```

Replace the numbers with your IP. Type `yes` if asked about a fingerprint. Enter the password if you were emailed one. You should now see a prompt like `root@ubuntu:~#`. Everything below is typed at that prompt.

---

## 4. Install Docker (5 minutes)

```sh
apt-get update && apt-get install -y ca-certificates curl git
curl -fsSL https://get.docker.com | sh
```

Check:

```sh
docker compose version
```

You should see a version like `Docker Compose version v2.xx`.

---

## 5. Get the Lab Tracker files (1 minute)

```sh
git clone https://github.com/gaurava-rgb/labtracker-odoo /opt/labtracker
cd /opt/labtracker
```

---

## 6. Create the secrets file (2 minutes)

```sh
./make-env.sh
```

This writes a file called `.env` with random passwords. Now put in your two values:

```sh
nano .env
```

Change these two lines, nothing else:

```
DOMAIN=lab.yourdomain.com
COMPANY_NAME=Your Lab Name
```

Save: press `Ctrl+O`, `Enter`, then `Ctrl+X`.

**Keep `.env` private.** It holds every password. Never email it, never put it in a chat, never commit it to Git.

---

## 7. Start it (3 minutes of waiting)

```sh
docker compose --profile tls --profile backup up -d
```

The first run downloads about 2.5 GB. Then watch it set itself up:

```sh
docker compose logs -f odoo
```

Wait until you see a line containing **`provisioning finished`** (about 3 minutes after the download). Press `Ctrl+C` to stop watching. The app keeps running.

---

## 8. First login (3 minutes)

1. Open **https://lab.yourdomain.com** in a browser. The padlock should be closed; the certificate was issued automatically.
2. Show the admin password on the server:
   ```sh
   grep ^ADMIN_PASSWORD .env
   ```
3. Log in with email **`admin`** and that password.
4. **Change it now:** click the avatar top right, **Preferences**, tab **Account Security**, **Change password**.

You should see the Odoo home with the apps Lab, Contacts, CRM, Project, Maintenance. Click **Lab** for the dashboard, or **Project** for the request board.

---

## 9. Add the people (5 minutes)

Settings, **Users & Companies**, **Users**, **New**.

- Name and email (the email is their login).
- On the **Access Rights** tab, under **Lab**, pick one:
  - **Manager**: everything in the four apps. Give this to the lab owner and anyone who runs the lab day to day.
  - **Coordinator**: sees everything, edits requests and samples, cannot change instruments or clients.
  - **Intern**: sees only work assigned to them, can tick boxes and add notes.
- Save. Click **Send an invitation email** if email is set up, or set a password yourself with **Change Password** under the Actions menu.

Only `admin` has system settings. Keep that login for the owner.

---

## 10. Put in the instruments and clients (15 minutes)

- **Maintenance, Equipment, New**: name, serial or code, and the **Condition** field. Anything not "Available" cannot be put on a request.
- **Contacts, New**: tick **Company** for a client organisation. Add a reference code if the lab uses one.
- **Project, Lab requests**: create the first request. It gets a `LAB-001` code by itself. Add samples on the **Samples** tab.

Old data from a previous tracker can be imported later from a spreadsheet; ask for help with that.

---

## 11. Things that run on their own

| What | When | Where |
|---|---|---|
| Nightly backup | 03:10 server time | `/opt/labtracker/backups`, last 14 days kept |
| Certificate renewal | automatic | handled by Caddy |
| Read-only copy of the database for the dashboard | continuous | inside Docker, no action needed |

Copy backups somewhere off the server once a week. Simplest: from your laptop,

```sh
scp root@203.0.113.10:/opt/labtracker/backups/*.dump ~/lab-backups/
```

---

## 12. Routine commands

All run as root in `/opt/labtracker`.

| Need | Command |
|---|---|
| Is everything running? | `docker compose ps` (all should say `running` or `healthy`) |
| See what Odoo is doing | `docker compose logs -f odoo` |
| Restart everything | `docker compose --profile tls --profile backup restart` |
| Stop everything | `docker compose --profile tls --profile backup down` |
| Start again | `docker compose --profile tls --profile backup up -d` |
| Back up right now | `scripts/backup-now.sh` |
| Restore a backup | `scripts/restore.sh backups/labtracker-2026-10-29.dump` |
| Check the replica is in sync | `scripts/check-replica.sh` |
| Self-check after an update | `scripts/verify.sh` |

---

## 13. Updating to a newer version

```sh
cd /opt/labtracker
git pull
docker compose --profile tls --profile backup pull
docker compose --profile tls --profile backup up -d
```

Odoo restarts, applies any new rules by itself, and comes back in about a minute. Take a backup first if you are nervous: `scripts/backup-now.sh`.

---

## 14. Troubleshooting

**The browser says "site cannot be reached" or shows a certificate warning**
- DNS is not pointing here yet. Re-run the `ping` check from step 2.
- Ports 80 and 443 must be open. Some providers have a firewall panel; allow both.
- Cloudflare proxy (orange cloud) was on during first start. Turn it grey, then `docker compose restart caddy`.

**The page loads but says "Internal Server Error" or spins forever**
- It is probably still setting up. `docker compose logs -f odoo` and wait for `provisioning finished`.

**I forgot the admin password**
- If you have not changed it yet: `grep ^ADMIN_PASSWORD .env`.
- If you did change it: `docker compose exec odoo odoo shell -d labtracker --no-http` then type
  `env['res.users'].browse(2).write({'password': 'NewPassword123'}); env.cr.commit()` and `exit()`.

**Out of disk space**
- Old backups are deleted after 14 days. Lower `BACKUP_KEEP_DAYS` in `.env` and restart.
- `docker system prune -f` removes unused Docker leftovers safely.

**I want to start completely over**
- `docker compose --profile tls --profile backup down -v` deletes the database too. Then step 7 again. Only do this before real data exists.

**Something else**
- Collect `docker compose ps` and the last 50 lines of `docker compose logs odoo`, and send them to whoever supports you. Do not send `.env`.
