# 20 — Store Opening Test Plan

Covers all 31 scenarios required by §16.16. Every case states its **verification layers** —
§16.16 requires UI, backend, database, permissions, task history, procurement linkage, asset
linkage and audit records, so each is named explicitly rather than assumed.

| Layer | Meaning |
|---|---|
| **UI** | desk form / workspace behaviour, exercised in the browser or via the same whitelisted endpoint the client calls |
| **BE** | server API called directly, bypassing the client, to prove the rule is not front-end only |
| **DB** | row-level assertion with SQL, not through the ORM |
| **PERM** | executed **as a non-bypass user**, never as Administrator |
| **HIST** | `Version` rows and `CH Store Opening Audit Log` |
| **PROC** | MR → RFQ → SQ → PO → PR → PI → Payment Entry linkage |
| **ASSET** | `Asset` / `Asset Movement` linkage |

---

## 20.1 Test environment and known bench constraints

`site_config.json` already carries `"allow_tests": true` and `"developer_mode": 1`.

**Four constraints this bench has already proved the hard way. Ignoring any of them produces a
green suite that tests nothing:**

1. **`bench run-tests --module` finds 0 tests here.** Load suites through
   `unittest.TestLoader` inside `bench console` instead. A "0 tests, 0 failures" result is the
   symptom, not a pass.
2. **Running suites outside the framework runner commits fixtures into ~33 tables.**
   `bench backup` before a full run and restore afterwards; a 0-table row-count diff is the proof
   of a clean restore.
3. **A permission test that only asserts the guard was *imported* is a permanently green test.**
   Eight report-scope leaks survived that way on this bench. Every PERM case below runs as a real
   restricted user and asserts on returned rows.
4. **A `has_field` probe on a typo'd fieldname is also permanently green.** Field-existence
   assertions must be made against the fixture definition, not a `has_column` call.

Additionally: `System Manager` bypasses **role** permissions but **not user permissions** — so it
is not a substitute for the intended role in any PERM case; and `frappe.throw` inside a helper
that is later caught does **not** clear `message_log`, so tests asserting "no user-facing error"
must inspect `frappe.message_log`, not just the absence of an exception.

**Fixtures.** One company, one `CH State`, two `CH City` rows (for cross-scope tests), one
`CH Store Format`, one `Project Template` with a reduced 20-task tree (the full 164-task template
is exercised only in TC-04), two users: `pm@test` (CH Project Manager) and `owner@test`
(CH Projects Team Member, scoped to one city only).

---

## 20.2 Test cases

### TC-01 — Creation of a store-opening proposal
**Layers:** UI, BE, DB, PERM
1. As `owner@test`, create `CH Store Opening` with company, brand, format, state, city, address,
   coordinates, proposed opening datetime. Save.
2. Assert `name` matches `SO-{YY}{MM}-{####}` and `docstatus = 0`, `stage = Draft`.
3. **DB:** row exists in `tabCH Store Opening`; `project` and `ch_store` are NULL.
4. **PERM:** `owner@test` can create; a user with only `CH Projects Viewer` gets
   `PermissionError` on create.
5. **Negative:** saving without `company`, `city` or `proposed_opening_datetime` throws
   `MandatoryError`.

### TC-02 — Approval and rejection
**Layers:** UI, BE, DB, PERM, HIST
1. Advance to `Awaiting Approval`. As `owner@test`, attempt the transition to `Approved` →
   expect refusal (not in `Workflow Transition.allowed`).
2. As `CH Store Opening Approver`, approve. Assert `docstatus = 1`,
   `approved_opening_datetime` set, `stage = Approved`.
3. **HIST:** a `CH Store Opening Audit Log` row exists with `from_stage = Awaiting Approval`,
   `to_stage = Approved`, the approver's user and a timestamp.
4. **Rejection path:** on a second proposal, reject without a `rejection_reason` → throws.
   Reject with a reason → `stage = Draft`, reason persisted, audit row written, **and no
   `Project` was created**.
5. **DB:** `SELECT COUNT(*) FROM tabProject` is unchanged by the rejection — the §16.14
   requirement that rejected proposals never become Projects.

### TC-03 — Project creation from an approved proposal
**Layers:** BE, DB, HIST
1. On approval, assert exactly one `Project` exists with `ch_store_opening = <SO>`.
2. Assert `CH Store Opening.project` is set and is **read-only** in every DocPerm.
3. **Idempotency:** call the creation entry point a second time → still exactly one Project;
   no exception; a `CH Store Opening Provision Log` row records the skip.
4. **DB:** the unique index on `tabCH Store Opening.project` rejects a manual duplicate.

### TC-04 — Project Template application
**Layers:** BE, DB
1. Approve with `CH Store Format.default_project_template` set to the full template.
2. Assert 164 `Task` rows exist for the project, 10 of which have `is_group = 1`.
3. Assert every task's `parent_task` points at a task **of this project**, never at a template
   task — this is the `check_for_parent_tasks` remap and it is the most common template bug.
4. Assert `Task Depends On` rows likewise point within the project.
5. Assert `ch_is_mandatory` is 1 on 145 tasks and `is_milestone` on 22 (report `19 §19.12`).
6. **Requirement rules:** with a rule scoped to a *different* state, assert its task is **absent**;
   with a rule matching this state, assert its task is **present** and mandatory.

### TC-05 — Task assignment
**Layers:** UI, BE, DB, PERM
1. Assign W3.6 to `owner@test`; assert a `ToDo` row exists with
   `reference_type = Task`, `allocated_to = owner@test`.
2. Complete the task; assert the `ToDo` is closed (`close_all_assignments` runs in
   ERPNext's `validate_status`).
3. **Assignment Rule:** configure one by `department`; create a task; assert auto-assignment.
4. **PERM:** `owner@test` can update their assigned task and **cannot** update an unassigned one.

### TC-06 — Task dependencies (finish-to-start)
**Layers:** UI, BE, DB
1. Attempt to complete W3.7 while W3.6 is Open → expect ERPNext's own throw ("Cannot complete
   task … as its dependant task … are not completed / cancelled").
2. Complete W3.6, then W3.7 → succeeds.
3. **BE:** the same attempt through the API, not the form, throws identically — proving the rule
   is server-side.
4. **Negative:** a circular dependency raises `CircularReferenceError`.

### TC-07 — Parallel tasks
**Layers:** BE, DB
1. Assert W3.8, W3.9 and W3.11 have **no** mutual `Task Depends On` rows.
2. Complete them in reverse order; all succeed. Parallelism is the absence of a dependency row,
   and this test exists to stop someone "helpfully" adding one.

### TC-08 — Critical-task delay
**Layers:** BE, DB, HIST
1. Push W3.6's `exp_end_date` out by 10 days.
2. Assert successors' earliest start is recomputed and **reported**, and that dates were **not
   silently rewritten** — §16.4. Assert a `CH Store Opening Date Revision` row exists with
   `previous_datetime`, `revised_datetime`, `slip_days = 10`, `reason` and `revised_by`.
3. **Negative:** a revision with no `reason` throws.
4. Assert ERPNext's `reschedule_dependent_tasks` did **not** move a successor in `Working`
   status behind the project's back (the silent-desync case in report `14`, Gap G2).

### TC-09 — Opening-date-at-risk calculation
**Layers:** BE, DB
1. With float on the critical path, assert `opening_date_at_risk = 0`.
2. Delay a critical task past the remaining float; assert `opening_date_at_risk = 1` and
   `project_health` degrades.
3. Delay a **non-critical** task by the same amount; assert `opening_date_at_risk` stays 0 —
   this distinguishes a real critical-path engine from a naive "any task is late" check.
4. Assert `ch_total_float` on a W9 marketing task is > 0 and on a W3 civil task is 0.

### TC-10 — Evidence-required completion
**Layers:** UI, **BE**, DB
1. On W2.5 (`ch_requires_evidence = 1`), set `status = Completed` with no attachment → throws.
2. Attach a file; complete → succeeds.
3. **BE — the case that matters:** call the same completion through
   `frappe.client.set_value` and through `set_multiple_status` (ERPNext's bulk path).
   **Both must throw.** §16.3 requires server-side validation, and a `before_save`-only or
   client-only guard passes step 1 while failing this one.
4. **Checklist:** a task with a mandatory `CH Task Checklist Item` not `is_done` cannot complete.
5. **Approval:** a task with `ch_approval_status = Pending` cannot complete.

### TC-11 — Unauthorized task completion
**Layers:** PERM, BE
1. As a user with no assignment and no role on the project, complete a task → `PermissionError`.
2. As `owner@test` (assigned), complete → succeeds.
3. As `owner@test`, attempt to set `ch_approved_by` / `ch_approval_status = Approved` on their
   own task → refused (approval is the approver's field, and self-approval is the classic
   segregation-of-duties hole).

### TC-12 — Department approval
**Layers:** UI, BE, DB, PERM, HIST
1. As `CH Department Head` (Legal), approve the Legal `CH Store Opening Signoff` row →
   `status = Approved`, `signed_on` and `approver` stamped.
2. As the same user, attempt to approve the **IT** signoff row → refused.
3. Assert the *Awaiting Go-Live Approval → Ready to Open* transition is refused while any signoff
   is Pending.

### TC-13 — Budget overrun
**Layers:** BE, DB, PROC
1. Create `Budget` with `budget_against = Project`, `applicable_on_purchase_order = 1`,
   `action_if_annual_budget_exceeded = Stop`, amount 100,000.
2. Submit a PO of 90,000 against the project → succeeds.
3. Submit a second PO of 20,000 → **ERPNext blocks it**. Assert the exception comes from
   ERPNext's budget validation, not from custom code — this test exists to prove we did **not**
   write a second budget engine.
4. Set the action to `Warn`; assert the PO submits and a warning is raised.
5. Assert `CH Store Opening.budget_variance` recomputes on the next readiness run and that the
   *displayed* value carries `readiness_run_on`.

### TC-14 — Purchase linkage
**Layers:** BE, DB, PROC
1. Run MR → RFQ → SQ → PO → PR → PI → Payment Entry, all stamped with the project.
2. **DB:** assert `project` is set on `Material Request Item`, `Supplier Quotation Item`,
   `Purchase Order Item`, `Purchase Receipt Item`, `Purchase Invoice Item`, and `ch_project` on
   the `Request for Quotation` header (the custom field that closes Gap G10).
3. Assert `Project.total_purchase_cost` reflects the PI, maintained by ERPNext.
4. Assert `committed_cost` on the store opening equals the submitted-PO total minus received.
5. **Duplicate-procurement guard:** raise a second open MR against the same `Task` → refused.
6. **Closure guard:** attempt *Handover → Closed* with an unpaid PI → refused; with an
   unreceived PO → refused.

### TC-15 — Asset linkage
**Layers:** BE, DB, ASSET
1. Receive a fixed-asset Item on a PR against the project; assert ERPNext auto-creates the `Asset`.
2. Assert `Asset.ch_project` and `Asset.ch_store` are stamped (Gap G9's fix).
3. **Negative:** create an `Asset` manually with neither set and a project-linked PR → refused by
   the `Asset.validate` guard (§16.7 "no asset creation without project/store mapping").
4. Assert `Asset.ch_asset_tag` is stamped by the existing ch_assets hook and that a duplicate tag
   is refused.
5. Record an `Asset Movement` to the store's location; assert custody.
6. **Closure:** attempt *Handover → Closed* with an unverified asset → refused.

### TC-16 — Branch creation
**Layers:** BE, DB, HIST
*Runs only under decision **B1** (report `17 §17.6`). Under B2, assert the field is absent and
no Branch is created.*
1. Complete W4.19; assert exactly one `Branch` exists, named on `store_code`, with `ch_company`,
   `ch_city`, `ch_zone` populated.
2. Assert a `CH Store Opening Provision Log` row `action = Branch`, `status = Success`,
   `created_document` set.
3. Re-run → no second Branch; log shows `Skipped`; **no exception** (retry-safety).

### TC-17 — Warehouse creation
**Layers:** BE, DB
1. Complete W4.20; assert the store's `Warehouse` tree exists via
   `ch_store.ensure_store_bins`, with the expected bin names under the store's group.
2. Assert the store's sellable warehouse is linked on `CH Store.warehouse`.
3. Re-run → idempotent; log `Skipped`.
4. **Negative:** assert no *phantom* `Bin` rows were created for warehouses with no stock — this
   bench has previously carried ~1,826 phantom Bins worth ≈₹14.5cr of ghost value.

### TC-18 — Cost-centre creation
**Layers:** BE, DB
1. Complete W4.21; assert the region and store `Cost Center` nodes exist via
   `ensure_store_cost_center_hierarchy`, under the company's root.
2. Assert `assign_pos_profile_cost_center` bound the profile without repointing an explicit
   pre-existing assignment.
3. Complete W4.23; assert `accounts_setup.setup_company_accounts` created/confirmed the company
   default **accounts** and payment-mode wiring, and did not duplicate any account.
4. Re-run both → idempotent.

### TC-19 — POS Profile creation
**Layers:** BE, DB, PERM
1. Complete W4.22; assert a `POS Profile` named `POS - {store_code}` exists with permissions.
2. Assert `CH Store.pos_profile` points at it and `cost_center` is assigned.
3. Assert the store code itself was generated from `Company.store_code_prefix` and that
   `store_id` came from `next_free_numeric_id("store")` — **not** from a raw `getseries`.
4. **Regression on a known live bug:** after a database restore, assert `store_id` allocation
   does not collide with a stored MAX (the `tabSeries`-lag defect that produced "Device ID must
   be unique" at billing).

### TC-20 — Duplicate-master prevention
**Layers:** BE, DB
1. Run the full provisioning sequence twice, back to back.
2. **DB:** assert counts of `Branch`, `Warehouse`, `Cost Center`, `POS Profile` and `CH Store`
   for this store opening are each exactly the expected number after both runs.
3. Assert the **unique index** on `tabCH Store Opening Provision Log (store_opening, action)`
   rejects a duplicate log row at the database level.
4. **Concurrency:** fire two provisioning calls in parallel; assert exactly one master of each
   type exists and the loser records a clean skip rather than an `IntegrityError` traceback.
5. Assert no provisioning ran inside a single `@atomic` block — a forced failure on the fourth
   action must leave the first three **committed**, not rolled back.

### TC-21 — Go-live blocked by incomplete critical task
**Layers:** UI, BE, DB
1. With one `ch_is_mandatory` task Open, run readiness; assert `blocker_count > 0` and
   `go_no_go = "No Go"`.
2. Attempt *Awaiting Go-Live Approval → Ready to Open* → **refused**, naming the blocking task.
3. **The anti-average case:** complete 160 of 164 tasks so `readiness_percent > 97`, leaving one
   mandatory fire-safety task open. Assert the transition is **still refused**. §16.6's core
   requirement — a high percentage must not open a store.
4. Also assert refusal for: an expired `blocks_go_live` document, a Pending department signoff,
   and unreceived opening stock.

### TC-22 — Go-live approval
**Layers:** UI, BE, DB, PERM, HIST
1. Clear all blockers; run readiness; assert `blocker_count = 0`.
2. As `CH Project Manager`, attempt the transition → refused (wrong role).
3. As `CH Store Opening Approver` → succeeds; `stage = Ready to Open`.
4. **HIST:** the audit row records `blocker_count_at_transition = 0` and
   `readiness_percent_at_transition` — the point-in-time evidence the approval was justified.
5. **Stale-approval guard:** reverse to *Awaiting Go-Live Approval*; assert `readiness_run_on` is
   cleared and the transition must be re-earned.

### TC-23 — Store opening
**Layers:** BE, DB
1. Transition *Ready to Open → Opened*.
2. Assert readiness was **re-run** at this transition and refused on a non-zero blocker count
   (guards against a blocker appearing between approval and opening).
3. **DB:** `CH Store.store_status = 'Active'` and `CH Store.opening_date` = the approved date —
   the field that is NULL on all 56 existing stores today.
4. Assert `CH Store.ch_store_opening` back-links to the proposal.

### TC-24 — Projects Team handover to Operations
**Layers:** BE, DB, PERM, HIST
1. Transition *Stabilisation → Handover* as Operations Manager.
2. Assert every handover `CH Store Opening Signoff` row is Approved, else refused.
3. **PERM:** after handover, `CH Project Manager` retains read but loses write on the proposal.
4. **HIST:** audit row records the handover with both parties.

### TC-25 — Stabilisation-period issues
**Layers:** BE, DB
1. Create `Issue` rows with `ch_store_opening` set across several `Issue Type` categories.
2. Assert they appear on the workspace's post-opening card.
3. Attempt *Stabilisation → Handover* with a blocking Issue open → refused.
4. Assert the stabilisation window honours `stabilisation_days` and does not auto-advance early.

### TC-26 — Project closure
**Layers:** BE, DB, PROC, ASSET, HIST
1. Attempt *Handover → Closed* with an open PO → refused; with an unpaid PI → refused; with an
   unverified asset → refused; with a missing mandatory document → refused.
2. Clear all; submit `CH Store Opening Closure` with `variance_explanation` and at least one
   `CH Store Opening Delay Cause`; close.
3. **DB:** assert `lead_time_days` is stored and that the delay cause is a **link**, so the
   §16.11 "repeated causes of delay" report is a `GROUP BY`.
4. Assert `Project.status = 'Completed'`.
5. Assert the whole record — proposal, tasks, documents, audit — is still readable after closure.

### TC-27 — Cancellation
**Layers:** BE, DB, PERM, HIST
1. Cancel without a reason → throws. With a reason → `docstatus = 2`, `stage = Cancelled`.
2. Assert the linked `Project` is set to Cancelled and its tasks to Cancelled, **not deleted**.
3. **PERM:** only `CH Store Opening Approver` may cancel.
4. Assert cancellation from `Opened` is **refused** — a trading store cannot be cancelled, only
   closed.
5. **HIST:** audit row with reason and user.

### TC-28 — Reopening
**Layers:** BE, DB, HIST
1. Reverse *Ready to Open → Awaiting Go-Live Approval* with a reason; assert allowed backwards by
   one stage only, and that a two-stage jump is refused.
2. Assert reversal out of `Opened`, `Closed` and `Cancelled` is **refused** in all three cases.
3. Assert `readiness_run_on` is cleared on reversal.
4. **Amend path:** amend a cancelled proposal; assert `amended_from` is set and the new document
   starts at Draft with no Project attached.

### TC-29 — Cross-company and cross-branch permission attempts
**Layers:** **PERM**, BE, DB
*The most important case in this plan. Run every step as a real restricted user.*
1. Create `CH User Scope` for `owner@test` limited to Company A, City X.
2. Create proposals in (A, X), (A, Y) and (B, X).
3. As `owner@test`, `frappe.get_list("CH Store Opening")` returns **only** (A, X).
4. As `owner@test`, `frappe.get_doc` on the (B, X) proposal raises `PermissionError` — proving
   `has_permission` guards direct-by-name reads, not just the list query.
5. Repeat for `CH Store Opening Document` and for `Task`.
6. **The silent-empty-list check:** assert the (A, X) result is **non-empty**. A guard bug returns
   `[]` for everyone and every "cannot see other companies" assertion still passes. This bench has
   had exactly that failure return `[]` for 51 users.
7. **Permlevel:** as `owner@test`, assert `budget_amount`, `committed_cost`, `invoiced_cost`,
   `amount_paid`, `budget_variance` and `Task.ch_budgeted_cost` / `ch_supplier` are **absent from
   the response**, not merely hidden in the form. As `CH Project Manager`, assert they are present.
8. Assert `System Manager` does **not** rescue an out-of-scope read created by a user permission.
9. Assert no `frappe.message_log` entries leaked from the filter loop (the swallowed-`msgprint`
   defect).

### TC-30 — Concurrent updates
**Layers:** BE, DB
1. Two users save the same proposal from stale copies → the second gets
   `TimestampMismatchError` (Frappe's optimistic lock). Assert no silent overwrite.
2. Two users complete two different tasks simultaneously → both succeed and
   `Project.percent_complete` is correct afterwards, not off by one.
3. Two parallel readiness runs → assert one consistent stored result, and that `readiness_run_on`
   reflects the later run.
4. Provisioning concurrency is covered by TC-20.4.

### TC-31 — Notification and escalation behaviour
**Layers:** BE, DB
1. Assign a task → assert exactly **one** notification to the assignee.
2. Overdue task → assert it lands in the **daily digest**, not as an immediate send.
3. Push the same task past its 🔴 SLA tier → assert it is **promoted** to an immediate send and
   is **not** also present in that day's digest (no double-send).
4. Opening-date-at-risk → immediate to PM, sponsor and Management.
5. Budget crosses 80% then 100% → two notifications, not one per PO.
6. **Flood test:** make 50 tasks overdue for one owner → assert **one** digest email, not 50.
   §16.10's "avoid notification flooding" is a testable assertion, not an aspiration.
7. Escalation ladder: owner → 24h department head → 48h PM → 72h sponsor. Assert each rung fires
   once and only once.
8. **Fail-closed:** with `CH Notification Settings` unconfigured for a company, assert the router
   sends to **nobody** rather than to everybody.

---

## 20.3 Coverage against §16.16

| §16.16 requirement | Case |
|---|---|
| Creation of a store-opening proposal | TC-01 |
| Approval and rejection | TC-02 |
| Project creation from an approved proposal | TC-03 |
| Project Template application | TC-04 |
| Task assignment | TC-05 |
| Task dependencies | TC-06 |
| Parallel tasks | TC-07 |
| Critical-task delay | TC-08 |
| Opening-date-at-risk calculation | TC-09 |
| Evidence-required completion | TC-10 |
| Unauthorized task completion | TC-11 |
| Department approval | TC-12 |
| Budget overrun | TC-13 |
| Purchase linkage | TC-14 |
| Asset linkage | TC-15 |
| Branch creation | TC-16 |
| Warehouse creation | TC-17 |
| Cost-centre creation | TC-18 |
| POS Profile creation | TC-19 |
| Duplicate-master prevention | TC-20 |
| Go-live blocked by incomplete critical task | TC-21 |
| Go-live approval | TC-22 |
| Store opening | TC-23 |
| Projects Team handover to Operations | TC-24 |
| Stabilisation-period issues | TC-25 |
| Project closure | TC-26 |
| Cancellation | TC-27 |
| Reopening | TC-28 |
| Cross-company and cross-branch permission attempts | TC-29 |
| Concurrent updates | TC-30 |
| Notification and escalation behaviour | TC-31 |

**Verification-layer coverage:** UI (10 cases), backend (31), database (26), permissions (9),
task history / audit (8), procurement linkage (3), asset linkage (2). All eight layers §16.16
names are exercised.

## 20.4 Test-suite structure

```
ch_projects/tests/
├── test_store_opening_lifecycle.py     TC-01,02,03,22,23,27,28
├── test_project_template.py            TC-04
├── test_task_controls.py               TC-05,06,07,10,11,12
├── test_schedule_control.py            TC-08,09
├── test_provisioning.py                TC-16,17,18,19,20
├── test_readiness_gate.py              TC-21,25,26
├── test_budget_and_procurement.py      TC-13,14
├── test_assets.py                      TC-15
├── test_handover_and_closure.py        TC-24,26
├── test_permissions_scope.py           TC-29        # non-bypass users only
├── test_concurrency.py                 TC-30
└── test_notifications.py               TC-31
```

## 20.5 Exit criteria

| # | Criterion |
|---|---|
| 1 | All 31 cases pass, **loaded via `unittest.TestLoader` in `bench console`** — a `bench run-tests --module` report of "0 tests" is not a pass |
| 2 | TC-10.3, TC-11, TC-21.3 and TC-29 pass **as restricted users**; TC-29.6 confirms the in-scope list is non-empty |
| 3 | TC-20 leaves exactly one of each master after two full runs, and the unique index is proven at the DB level |
| 4 | A `bench backup` / restore around the full run shows a **0-table row-count diff** |
| 5 | No `Error Log` rows created by a passing run |
| 6 | `bench migrate` on a site **without** `ch_erp15` fails in `before_install` with a message naming the missing app — not at `after_migrate` with `1146 tabCH Role Link doesn't exist` |
| 7 | `bench build` and a browser load of the workspace show all 14 elements — TC-04's shortcut `stats_filter` fieldnames are validated by loading the page, since Frappe never validates them |
