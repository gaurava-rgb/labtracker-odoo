# Synthetic test data and role logins

Run these from the checkout on your server (`/opt/labtracker`) while Odoo is running. They require Python 3 on the host and the provisioned Lab Tracker database. They stream code into the running container, so no image rebuild, admin password, or restart is needed.

## Create test data

```sh
scripts/seed-test-data.sh
```

Creates these three internal users with different permissions and random passwords:

| Login | Role | Intended permissions |
|---|---|---|
| `demo.manager@lab.invalid` | Lab Manager | Manage lab work, equipment, clients, and CRM; no system administration |
| `demo.coordinator@lab.invalid` | Lab Coordinator | Create/edit requests and samples; change equipment condition, Best practices and Maintenance procedures; create/edit/archive contacts and equipment contacts (no delete); type new contact types; read CRM |
| `demo.intern@lab.invalid` | Lab Intern | View/edit assigned requests and samples; add samples to own requests; cannot reassign, create requests, or delete tasks; Lab Home shows own work only |

These use the existing provisioned Lab groups; the script does not change security rules. It checks key permissions before committing, including the equipment checks: coordinators can change condition and Best practices but not name or serial number, can type a new contact type, and cannot delete contacts, equipment contacts or contact types. Those checks run in a rolled-back savepoint and add no contact types or equipment contacts to the fixture. These accounts are separate from the optional `manager@lab.test` accounts in the original provisioning script.

View their credentials privately on the server:

```sh
cat .test-data-credentials.json
```

The file is mode 600 and Git-ignored. Do not share or commit it. Keep it until cleanup; repeat seed runs do not reset passwords or overwrite existing data. If it is lost, reset the test users' passwords through Odoo or clean and reseed.

The fixture includes:

- Three synthetic client companies.
- Five instruments: initially three available, one under repair, one out of service; the centrifuge then becomes Under repair to exercise automatic blocking.
- Six requests covering overdue, unassigned, equipment-blocked, active, complete, and future work. Blocking comes from changing the polymer request's assigned centrifuge to Under repair after assignment, not from setting a stage.
- Twelve samples with mixed checklist states (Scan, Export, Email, Build in), numeric time taken, and assignments; the intern owns one request and its samples.
- Two maintenance requests and three CRM leads.
- Odoo's three automatically generated personal welcome tasks, tracked for cleanup too.

Business names contain `[TEST]`. Clients use reserved `.invalid` email addresses. Records have exact ownership markers in `ir.model.data` under `labtracker_test_data`; cleanup does not rely on matching names or email suffixes. Existing real records, projects, stages, permissions, and accounts are not adopted or overwritten.

Refresh the dashboard after seeding. Its replica may need a moment to catch up.

## Remove the fixture

Preview the exact cleanup operation without saving changes:

```sh
scripts/clean-test-data.sh --dry-run
```

Then remove it:

```sh
scripts/clean-test-data.sh
```

Cleanup deletes the owned requests/samples, instruments, maintenance records, CRM leads, users and their partner records, clients, welcome tasks, ownership/version markers, and attached chatter, activities, and attachments. After a successful commit it also deletes the private credentials file. It includes archived or renamed fixture records. Repeating cleanup is a no-op.

If a non-test business record references a fixture parent, user, contact, or instrument in the supported Lab models, cleanup refuses and rolls back rather than clearing the link or deleting real data. The error identifies the model, record ID, and field. Detach the link or deliberately remove that dependent record in Odoo, then retry. Do not turn test records into permanent client data; create separate real records instead.

Manually created records are not automatically owned just because a test user created them. Remove any such test-only records deliberately before cleanup if they reference the fixture.

Cleanup removes live application records, not historical server logs or backups. PostgreSQL/Odoo may retain normal audit metadata, and backups made while the fixture existed still contain it. It does not reset consumed database IDs or LAB request numbers: rewinding shared counters could collide with real work. Filestore attachment bytes are reclaimed by Odoo's normal garbage collection.

## Validate without leaving data behind

```sh
scripts/seed-test-data.sh --dry-run
scripts/seed-test-data.sh --check-cycle
```

The first exercises creation and role checks, then rolls back. The second additionally checks repeat seed behavior, refusal to delete real dependent tasks, removal of renamed/archived records, and repeat cleanup. It requires that no fixture is already loaded. Both leave no committed test data or credentials file; database identity counters may still advance.

Both seed and cleanup use a single transaction and a shared advisory lock. A failure rolls back the entire operation. Use these scripts for a testing window and clean the fixture before using the service for real lab work. They do not touch nginx, Docker volumes, the email tracker, or Hermes.
