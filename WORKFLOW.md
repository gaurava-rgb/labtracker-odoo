# Lab request workflow

Top-level requests in the Lab requests project follow **New → Active → Complete → Pending pickup**. These rules run in the Odoo model, so they apply to forms, kanban moves, imports, and API writes.

- Requests are created at New. Creating directly at Active or Complete is rejected.
- Each transition must advance exactly one stage. Skipping, reversing, or reopening completed requests is rejected.
- Marking a request Done uses the same completion transition (Active → Complete); it cannot bypass the sequence. On a request that is already Complete or Pending pickup, Done changes nothing. Stage and task status are kept consistent: Complete and Pending pickup are both "done" (`1_done`), New and Active are in progress.
- Converting another task into a request, or moving a request out of its workflow, is rejected to prevent bypasses.
- Other projects and personal tasks are unaffected. Samples retain their existing checklist-driven completion/reopening behavior.

## Blocking is separate from stage

`lab_is_blocked` and `lab_block_reason` are stored, computed, read-only fields. They cannot be set manually through the UI or ordinary API calls. The reason identifies the equipment and its condition.

An unfinished, active request is blocked if its assigned equipment, or equipment on one of its unfinished, active samples, is unavailable or archived. Samples without their own equipment use their parent's equipment. Unknown conditions on assigned equipment also block work.

A blocked request remains at its current stage. It cannot advance until the equipment is available again or its equipment assignment is corrected. Notes and other ordinary edits remain possible. Equipment changes recalculate the flags, and the dashboard and Blocked filter use those flags. A completed sample's equipment does not block an unfinished parent request unless that equipment is also assigned directly to the parent.

Unavailable equipment can now be put on a request or sample (owner decision, November 21, 2026). The old automation "Lab: block equipment that is down" is deleted by provisioning. The equipment dropdown lists every instrument; one that is not available shows its condition, for example "Centrifuge (Under repair)". Equipment already on a request or sample stays there when its condition changes; only the blocking flag changes. Missing equipment, missing assignees, and overdue deadlines are not newly introduced blockers. Additional blocking conditions remain in [TO_CHECK.md](TO_CHECK.md).

<!-- BEGIN tasks changes 2026-11-21 -->
## Pending pickup

- **Complete** means the lab work is done. **Pending pickup** means the work is done and the client has not collected it yet.
- A request moves Complete → Pending pickup one step forward, like every other stage. It cannot go back to Complete or be reopened.
- The dashboard's open count, the Open requests, Overdue, Unassigned, Blocked, Due in 14 days and Needs attention filters count only New and Active requests. Complete and Pending pickup are both finished work.
- Lab Home has a **Pending pickup** tile, and there is a saved **Pending pickup** filter.
- Blocking is never calculated for Complete or Pending pickup requests, or for samples in either stage.
- No stage is folded in the kanban view any more, because Complete is no longer the last column.
- There is no final "Picked up" stage. A collected request stays in Pending pickup. Archive it if it should leave the board; see [TO_CHECK.md](TO_CHECK.md).

## Sample checklist and time taken

- A sample has four toggles: **Scan**, **Export**, **Email** and **Build in**. The sample completes automatically when all four are on, and goes back to Active when one is turned off.
- Existing completed samples are not changed. Build in starts off on them; if someone later changes one of their toggles, Build in must be on for the sample to stay complete.
- **Time taken (h)** on a sample is a number of hours typed by hand, shown as hours:minutes (1.5 shows as 01:30). Nothing is timed automatically.
- The request shows **Total time taken (h)**: the sum of its active samples' time. It is calculated and stored, and is read-only.
- Before this change, time was a free-text box ("1.5 h") with no calculation. The 20.0.2.2.0 upgrade copies readable old text ("1.5 h", "90 min", "1h 30m", "1:30") into the number. Text it cannot read stays in the hidden old field "Time spent (old text)" and gets a note in the sample's chatter.
<!-- END tasks changes 2026-11-21 -->

## QA follow-ups (November 22, 2026)

- **Total time taken** cannot be set by hand, including over the API. A direct write is rejected, the same way the Blocked flag is protected. Only the sum of the samples sets it.
- **Time taken** is shown only on samples. It is hidden on a top-level request, so the request form shows only the read-only total.
- The saved-filter kanban of requests under Project is now called **Requests board**. It used to be called "Lab home", which was easy to confuse with the **Lab Home** dashboard. Provisioning finds the menu and action by either name, so the rename is safe to run again.
- The Intern assignee guard now lives in the lab_dashboard model, not in an automation rule. A blocked change shows the same message, but no longer writes an ERROR line to the server log. Provisioning deletes the old automation "Lab intern: cannot change assignee".

## Completion condition deferred

The proposed requirement for at least one sample, with every sample complete, is **not enforced yet**. An Active, unblocked request can currently become Complete without samples. This remains a review item in [TO_CHECK.md](TO_CHECK.md).

## Existing records on upgrade

The 20.0.2.2.0 migration adds Pending pickup after Complete, unfolds Complete, and converts old time text. Existing Complete requests stay Complete; nothing is moved to Pending pickup automatically. The earlier module migration preserves existing tasks. Tasks previously at the manual Blocked stage move to Active; their blocking flags are calculated from actual equipment data. The old stage is detached from the Lab project (preserved elsewhere if shared). New, Active, and Complete are ordered explicitly. A former manual blocked reason in a title is not treated as a data condition.

## Verification

```sh
scripts/check-workflow.sh
scripts/verify.sh
```

The workflow script tests real model operations, including invalid transitions and equipment changes, then rolls back every test record. Database IDs may advance. The smoke test also checks the stage order and blocking fields.

Changes to the model, views, or CSS require rebuilding the Odoo image and upgrading `lab_dashboard`. The normal entrypoint upgrades the module when its version changes and reapplies provisioning when its marker changes.

Application add-ons ship under `/opt/labtracker/addons`. They deliberately do not live in the official image's `/mnt/extra-addons` volume: Compose can preserve that volume across upgrades and silently mask updated image code.

The current VPS deployment uses the locally built image `labtracker-odoo:workflow-20261111`, selected by `LABTRACKER_IMAGE` in its private `.env`. It has not been published to GHCR. Rebuild that local image when updating this source and recreate the Odoo service; do not use the original guide's image-pull update command until a published image includes these changes.
