# Lab Tracker role permissions

This describes the current implementation, including inherited Odoo permissions. It is a reference for what the code allows, not a proposal for stricter access. Checked against the provisioning code and the live VPS's access rules.

The three Lab roles are **Manager**, **Coordinator**, and **Intern**. **Administrator** is a separate Odoo system role. A person's job title or login name does not grant permissions; their assigned groups do.

The table assumes one Lab role, no extra application groups, and records in the user's permitted company/project scope. Additional groups, project privacy, record rules, and linked-record constraints can affect access.

## Permissions at a glance

| Area or action | Lab Manager | Lab Coordinator | Lab Intern |
|---|---|---|---|
| Open the Lab Home dashboard | Yes | Yes | Yes; numbers and lists cover only their own assigned work |
| See Lab requests and samples | All accessible Lab work | All accessible Lab work | Assigned work only; see assignment rules below |
| Create requests | Yes | Yes | No |
| Add samples/subtasks | Yes | Yes | Only under top-level requests assigned to them |
| Edit requests and samples | Yes | Yes | Assigned work; broader than checklists only |
| Change assignees | Yes | Yes | No |
| Delete requests or samples | Yes | Yes | No |
| Advance a request's stage | Yes, within workflow rules | Yes, within workflow rules | Assigned requests, within workflow rules |
| Read equipment and its condition | Yes | Yes | Yes |
| Create, edit, archive, or delete equipment | Yes | No | No |
| Change equipment condition (Available / Under repair / Out of service) | Yes | Yes | No |
| Edit equipment Best practices and Maintenance procedures pages | Yes | Yes | No; read only |
| Edit any other equipment field (name, serial number, category, notes, ...) | Yes | No | No |
| Read equipment contact persons (Contacts tab) | Yes | Yes | Yes |
| Add, edit, or archive equipment contact persons | Yes | Yes | No |
| Delete equipment contact persons | Yes | No | No |
| Pick or type a new equipment contact type (Sales, Support, Engineer, ...) | Yes | Yes | No |
| Rename or delete equipment contact types (Maintenance > Configuration) | Yes | No | No |
| Read client/contact records | Yes | Yes | Yes |
| Create, edit, or archive clients/other contacts | Yes | Yes | No |
| Delete clients/other contacts | Yes | No | No |
| Read CRM leads/opportunities | Yes | Yes | No |
| Create, edit, or delete CRM leads/opportunities | Yes | No | No |
| Read maintenance requests | All accessible records | All accessible records | Scoped inherited access; see below |
| Create or modify maintenance requests | Yes | No | Scoped inherited access; see below |
| Manage other users, passwords, or access rights | No | No | No |
| Install/uninstall apps or administer system settings | No | No | No |

“Samples” is the Lab interface's name for Odoo subtasks. They are not a separate task model.

## Lab Manager

Can manage the lab's operational data across Project, Maintenance, Contacts, and CRM: create and assign work, add samples, manage instruments and their condition, maintain clients, and manage leads. Can delete operational records where Odoo permits deletion.

Cannot administer users or application/system settings merely by having the Lab Manager role. The Lab Manager role includes application management permissions, not Odoo system administration.

Cannot skip or reverse the request workflow, or manually override the calculated blocking flag, through ordinary application operations.

## Lab Coordinator

Can see the dashboard and Lab work, create requests and samples, edit them, assign or reassign work, and delete requests/samples. Can read equipment, maintenance requests, contacts, and CRM to coordinate that work.

On equipment, can change the **condition** (Available / Under repair / Out of service), edit the **Best practices** and **Maintenance procedures** pages, and manage the **Contacts** tab. Nothing else on an equipment record. The server rejects a Coordinator write that touches any other equipment field (name, serial number, category, notes, archive, and so on), including over the API; on the equipment form every other field is shown read-only. Condition changes are logged in the equipment's chatter.

Can create, edit, and archive clients and other contacts, but cannot delete them. Can add, edit, and archive the contact persons listed on an equipment's **Contacts** tab, but cannot delete them. Each contact has a free-text **Type**: pick a suggestion (Sales, Support, Engineer) or type a new one, which is saved as a new type. Coordinators can add types but cannot delete them.

Cannot create, archive, or delete equipment, and cannot create, edit, or delete maintenance requests or CRM leads.

**Deletion of requests and samples is currently allowed.** There is no custom Coordinator restriction making those records non-deletable.

## Lab Intern

### Which tasks are visible?

A task is in scope when either:

1. The Intern is directly assigned to that task; or
2. The task's immediate parent request is assigned to that Intern.

For example, assigning a request to an Intern lets them see that request and its direct samples. Assigning just one sample does not automatically give them access to its parent request or sibling samples. Unrelated or unassigned Lab work is outside their task scope.

The rule uses the immediate parent; it is not a general grant to an arbitrarily deep hierarchy of descendants. Personal tasks assigned to the Intern can also satisfy the same rule.

### What can they edit?

Can edit in-scope tasks, tick sample checklists, enter time spent, and add notes using the available task/chatter controls. Request stage changes are allowed when the shared workflow rules permit them.

**The current code does not limit Interns to checklist and note fields.** It grants general task-update permission on in-scope records, with an explicit ban on changing assignees. Other writable task fields, such as names, deadlines, or equipment selection, are not individually restricted by a Lab field whitelist. Individual Odoo views or other validation rules may still make a field read-only or reject a change.

Cannot create requests, delete requests/samples, change assignees, or access CRM leads. Can add samples to requests assigned to them (see below). They cannot edit equipment (including Best practices and Maintenance procedures), equipment contact persons, contact types, or other contact records; they can read the equipment Contacts, Best practices and Maintenance procedures tabs.

Equipment and contact lookup access is not limited to items linked to their assigned requests. An Intern is not a Project-only sandbox: some standard internal-user access remains available.

<!-- BEGIN tasks changes 2026-11-21 -->
### Adding samples and Lab Home (since November 21, 2026)

- An Intern can create a sample under a top-level Lab request that is assigned to them. The access row "Lab intern: add samples to assigned requests" grants this, and nothing else.
- They cannot create a top-level request, a sample under someone else's request, or a sample under a sample.
- A new sample may be assigned only to the Intern or to people already on the request. After that, the assignee guard blocks any assignee change.
- Since November 22, 2026, the assignee guard is Python code in the lab_dashboard add-on, not an automation rule. An Intern write that changes the assignees of a request or sample is rejected with "Interns cannot change who a request or sample is assigned to." A write that leaves the assignees unchanged is allowed. The old automation "Lab intern: cannot change assignee" is deleted by provisioning, so a denial no longer logs an ERROR.
- They can edit the samples they add, like any other sample in scope. They still cannot delete anything.
- Lab Home opens for Interns. Their numbers are read live from the main database as themselves, so record rules apply: every tile and list covers only requests they can see. A purple "your work" badge shows this. Managers and Coordinators still get lab-wide numbers from the replica.
<!-- END tasks changes 2026-11-21 -->

### Maintenance requests: inherited exception

The Lab code does not add an Intern-specific prohibition on maintenance requests. Odoo's inherited access domain permits operations within the allowed company when the Intern is listed as its owner (`owner_user_id`), is an assignee on the request, or is a follower of it.

An Intern can create a maintenance request assigned to themselves, and read, edit, or delete maintenance requests within that scope. Creation and the resulting read/write/delete permissions were checked in a transaction that was rolled back.

This is scoped access, not permission to manage all maintenance requests. A request that does not meet those conditions is inaccessible. Menu visibility and creation defaults can affect what can be reached through the UI; hiding a menu does not itself remove the underlying model permission.

Do not assume that “Intern cannot create Lab requests/samples” also means “Intern cannot create any kind of record.” Maintenance requests are a different model.

## Odoo Administrator

The existing system administrator can manage users and their roles, reset other users' passwords, configure system/application settings, and install or uninstall modules, as well as manage lab data.

Normal UI/API operations still run through the custom request workflow. Being a system administrator does not provide a normal workflow button to skip stages or manually set a blocking reason. Database/server maintenance is separate from everyday application permissions.

All users can use ordinary self-service preferences/password controls where Odoo permits them. That does not grant administration of other accounts.

## Workflow rules apply across roles

- Top-level Lab requests start at **New** and advance **New → Active → Complete → Pending pickup**. Skipping, reversing, or reopening is rejected.
- Equipment-derived blocking is separate from stage and prevents request progression. Users cannot manually set its flag/reason through ordinary UI/API writes.
- Samples retain their checklist-driven completion/reopening behavior.
- Requiring at least one sample and requiring all samples complete before request completion are **deferred**, not enforced yet.

See [WORKFLOW.md](WORKFLOW.md) and [TO_CHECK.md](TO_CHECK.md).

## Assigning roles

An Administrator uses **Settings → Users & Companies → Users**, opens the user, and selects the Lab role in Access Rights. The Lab selector is separate from Odoo's general account-role selector.

Assign one Lab role per user. Do not stack roles to try to combine their permissions: Coordinator and Intern restrictions check group membership, so adding Manager does not necessarily cancel a restrictive role's rules. Review extra Project, Maintenance, Contacts, Sales, or Administrator groups when a user's access differs from this baseline.

The dashboard's menu is visible to Manager, Coordinator, Intern, and system administrators, and its data method separately enforces that list. Interns get numbers limited to their own work. Other menus and record access also depend on inherited Odoo groups; a hidden menu is not a complete permissions specification.

## Test accounts and implementation references

The synthetic-data script creates `demo.manager@lab.invalid`, `demo.coordinator@lab.invalid`, and `demo.intern@lab.invalid`. Passwords are random and stored privately in `.test-data-credentials.json`; see [TEST_DATA.md](TEST_DATA.md).

- [Role groups, restrictions, task scope, and assignee guard](init/lab_roles.py)
- [Coordinator equipment write guard, equipment contacts and contact types, Best practices / Maintenance procedures fields](addons/lab_dashboard/models/lab_equipment.py), [access rows](addons/lab_dashboard/security/ir.access.csv)
- [Dashboard access check](addons/lab_dashboard/models/lab_dashboard.py)
- [Request workflow and blocking enforcement](addons/lab_dashboard/models/lab_workflow.py)
- [Synthetic fixture permission checks](tools/test_data_orm.py)

The access audit for this document did not change role assignments or commit test records.
