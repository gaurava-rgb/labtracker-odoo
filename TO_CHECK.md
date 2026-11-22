# Workflow rules to check

## Request completion

- [ ] To check: require at least one sample before a Lab request can become Complete, and require all its samples to be complete.
- [ ] Enforce the agreed completion rule server-side for both UI and API operations; it is not enforced by the current code.

Recorded November 11, 2026. Implementation deferred while testing the current workflow.

## Stage progression and blocking

- [x] Enforce New → Active → Complete for top-level Lab requests on the server, including API writes. New requests start at New; skipping and reversing stages are rejected. Samples retain their checklist-driven lifecycle.
- [x] Remove the manual Blocked stage. Unavailable or archived assigned equipment (including equipment on unfinished samples) produces a separate calculated blocking flag/reason and prevents request progression. Restoring equipment availability clears the condition.
- [x] Add Pending pickup after Complete (November 21, 2026).
- [ ] Decide whether collected requests need a final "Picked up" stage, or whether archiving a Pending pickup request is enough.
- [ ] Define any additional structured blocking conditions. Missing equipment, unassigned work, and overdue deadlines are not automatically blocking conditions.

## Owner decisions 22 Nov 2026 (leave as is, revisit later)
- Interns may add samples to requests that are already Complete or Pending pickup. Accepted for now.
- Interns may add a sample with no assignee; no default assignment to the intern. Accepted for now.
- Open: a final "Picked up" stage after Pending pickup. Not decided.
