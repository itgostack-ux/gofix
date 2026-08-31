# 17 — Store Opening Workflow and Permissions

Covers §16.5 (stages and controlled workflow), §16.9 (roles and permissions) and §16.10
(notifications and escalation).

**Carrier of the lifecycle:** `CH Store Opening` (submittable). Not `Project` — see report
`16 §16.2`: `Project.validate()` recomputes `status` on every save and would overwrite any
workflow-driven stage, and `Project` has no `docstatus` to approve or reject against.

---

## 17.1 Stage model

§16.5 lists 18 stages and instructs: *"Confirm the final stages with the actual business before
hardcoding them."* The list below is the **proposal**, with the stages that should be questioned
marked **?**.

| # | Stage | `docstatus` | What it means |
|---:|---|:---:|---|
| 1 | Draft | 0 | proposal being written |
| 2 | Feasibility | 0 | catchment, sales projection, capex/opex estimate |
| 3 | Awaiting Approval | 0 | submitted for management decision |
| 4 | Approved | 1 | approved; **Project + tasks created here** |
| 5 | Property Finalisation | 1 | lease, deposit, landlord and building approvals |
| 6 | Planning & Design | 1 | layout, BOQ, contractor |
| 7 | Procurement | 1 | MR → RFQ → PO |
| 8 | Execution | 1 | civil, electrical, fitout |
| 9 | IT & Operational Setup | 1 | **masters created here** — store code, accounts, cost centre, warehouse, POS Profile |
| 10 | Pre-Opening Validation | 1 | snag closure, tests, department checks |
| 11 | Awaiting Go-Live Approval | 1 | readiness run published; go/no-go |
| 12 | Ready to Open | 1 | approved to open; **hard gate — see §17.2** |
| 13 | Opened | 1 | trading; `CH Store.store_status` → Active |
| 14 | Stabilisation | 1 | configurable support window |
| 15 | Handover | 1 | Projects Team → Operations |
| 16 | Closed | 1 | reconciled, lessons captured |
| 17 | On Hold | 1 | suspended from any active stage |
| 18 | Cancelled | 2 | abandoned |

**Questions for the business before this is built:**

| **?** | Question |
|---|---|
| 2 vs 3 | Is *Feasibility* a distinct stage or just Draft with more fields filled? If the same people do both with no handoff, merge them. |
| 5–8 | These four run **concurrently** in practice — property, design, procurement and execution overlap heavily. A single linear `stage` field cannot represent "fitout at 60% while the fire NOC is still pending". **Recommendation:** collapse 5–8 into one stage *Execution* and let **workstream readiness percentages** (§16.6) express the parallel detail, which is what they are for. This reduces 18 stages to 15 and removes the most likely source of a wrong `stage` value. |
| 12 | Is *Ready to Open* a real business state with a duration, or the same instant as go-live approval? If it lasts less than a day, fold it into 11. |
| 14 vs 15 | Does Handover happen **at** the end of stabilisation, or does stabilisation continue under Operations after handover? This changes who owns issues in week 3. |

**Do not build the workflow until these four are answered.** A `Workflow` with the wrong state set
is expensive to change once documents exist in the retired states.

## 17.2 Transition gates

Every transition defines the ten controls §16.5 requires. The `Workflow` doctype supplies
*authorized role* and *condition*; everything else is a **server-side guard**, in the pattern of
`ch_erp15/closure/guards.py`. A Workflow `condition` is a sandboxed expression evaluated on the
document — it cannot safely query tasks, documents or budgets, so it is used only for cheap field
checks.

Legend: **MT** mandatory tasks · **MD** mandatory documents · **AP** approval ·
**BV** budget validation · **SV** stock/procurement · **IT** IT validation ·
**FV** finance · **OV** operational · **N** notification · **AU** audit entry

| From → To | Authorized role | Guard content |
|---|---|---|
| Draft → Feasibility | Projects Team Member | **MD** location proposal attached · **N** PM · **AU** |
| Feasibility → Awaiting Approval | Project Manager | **MT** business case, catchment, sales projection, capex, opex complete · **BV** budget figure present and non-zero · **N** sponsor + Management Approver · **AU** |
| Awaiting Approval → **Approved** | Management Approver *(routed by `CH Approval Authority`: `doctype_target = CH Store Opening`, `action = Approve`, amount band on capex)* | **AP** required · sets `approved_opening_datetime` · **submits** the document · **triggers Project + template + requirement rules** · **N** all members · **AU** |
| Awaiting Approval → Draft (reject) | Management Approver | `rejection_reason` mandatory · **N** requester · **AU** |
| Approved → Property Finalisation | Project Manager | **N** Legal · **AU** |
| Property Finalisation → Planning & Design | Project Manager | **MT** lease signed, deposit paid · **MD** `CH Lease` submitted and linked; lease agreement document Verified · **FV** deposit has a Payment Entry · **AU** |
| Planning & Design → Procurement | Project Manager | **MT** design approval, BOQ · **BV** a `Budget` exists with `budget_against = Project` · **AU** |
| Procurement → Execution | Procurement Team | **SV** every mandatory procurement task has an MR or PO · **BV** committed cost ≤ approved budget · **AU** |
| Execution → IT & Operational Setup | Project Manager | **MT** civil, electrical, snag rectification complete · **MD** completion certificate · **AU** |
| IT & Operational Setup → Pre-Opening Validation | IT Team | **IT** store code, accounts, cost centre, warehouse and POS Profile all provisioned — read from `CH Store Opening Provision Log`, not re-derived · **AU** |
| Pre-Opening Validation → Awaiting Go-Live Approval | Project Manager | **readiness run executed within the last 24h** · **N** every section approver · **AU** |
| Awaiting Go-Live Approval → **Ready to Open** | Management Approver | **HARD: `blocker_count == 0`** · **MT** every `ch_is_mandatory` task Completed · **MD** every `blocks_go_live` document Verified and unexpired · **BV** no unapproved expenditure · **SV** opening stock received · **IT** all IT checks Pass · **FV** finance sign-off · **OV** staff readiness confirmed · **AP** all department sign-offs recorded · **N** all · **AU** |
| Ready to Open → **Opened** | Operations Team | **HARD: re-run readiness; `blocker_count == 0`** (stale-approval guard) · sets `CH Store.store_status = Active` and `CH Store.opening_date` · **AU** |
| Opened → Stabilisation | automatic on open | starts the `stabilisation_days` clock · **AU** |
| Stabilisation → Handover | Operations Team | **MT** no open blocking `Issue` · **AU** |
| Handover → Closed | Management Approver | **FV** budget reconciled · **SV** no open PO/MR, no unreceived material, no unpaid PI · asset reconciliation clean · **MD** document completeness · `CH Store Opening Closure` submitted · **AU** |
| *any active* → On Hold | Project Manager | `hold_reason` mandatory · **N** all · **AU** |
| On Hold → *stage held from* | Project Manager | **AU** |
| *any* → Cancelled | Management Approver | `cancellation_reason` mandatory · **cancels** the document · **N** all · **AU** |

**Reversal rules (§16.5).** Reversal is allowed **only** backwards by one stage, only by the
Project Manager or Management Approver, only with a recorded reason, and **never** out of
`Opened`, `Closed` or `Cancelled`. Reversing out of `Ready to Open` clears `readiness_run_on`,
so go-live must be re-approved — this is what stops a stale approval being reused after a scope
change. Every reversal writes a `CH Store Opening Audit Log` row.

## 17.3 Role model

§16.9 names 14 roles. Six exist on this site today; eight are new, following the bench's `CH `
convention.

| §16.9 role | Implementation | Status |
|---|---|---|
| Projects Team Member | `CH Projects Team Member` | new |
| Project Manager | `CH Project Manager` | new |
| Department Task Owner | *not a role* — Frappe **assignment** (`ToDo`) on the Task | reuse |
| Department Head | `CH Department Head` | new |
| Procurement Team | `Purchase Manager` / `CH Purchase Executive` | exists |
| Finance Team | `Accounts Manager` / `Finance Manager` | exists |
| IT Team | `CH IT Team` | new |
| HR Team | `HR Manager` / `CH People Admin` | exists |
| Marketing Team | `Marketing Manager` | exists |
| Operations Team | `Operations Manager` | exists |
| Store Manager | `CH Store Executive` | exists |
| Management Approver | `CH Store Opening Approver` | new |
| System Administrator | `System Manager` | exists |
| Auditor | `CH Store Opening Auditor` (read-only everywhere) | new |

Plus `CH Projects Viewer` for read-only stakeholders. ERPNext's own `Projects Manager` /
`Projects User` are retained for the underlying `Project`/`Task` DocPerms.

> **Bench-specific caution.** `System Manager` bypasses **role** permissions but **not user
> permissions**. This bench has already had a live incident where a `has_permission` hook plus
> strict user permissions returned `[]` for 51 users silently, and System Manager did not save
> them. Every permission decision below must be tested as the actual role, never as
> Administrator.

## 17.4 Permission matrix — `CH Store Opening`

| Role | Read | Write | Create | Submit | Cancel | Amend | permlevel 1 (financials) |
|---|:-:|:-:|:-:|:-:|:-:|:-:|:-:|
| CH Projects Team Member | ✓ | ✓ | ✓ | – | – | – | – |
| CH Project Manager | ✓ | ✓ | ✓ | ✓ | – | ✓ | ✓ |
| CH Store Opening Approver | ✓ | ✓ | – | ✓ | ✓ | – | ✓ |
| CH Department Head | ✓ | ✓¹ | – | – | – | – | – |
| CH IT Team | ✓ | ✓¹ | – | – | – | – | – |
| Purchase Manager | ✓ | ✓¹ | – | – | – | – | ✓ |
| Accounts Manager | ✓ | ✓¹ | – | – | – | – | ✓ |
| Operations Manager | ✓ | ✓¹ | – | – | – | – | – |
| CH Store Executive | ✓² | – | – | – | – | – | – |
| CH Store Opening Auditor | ✓ | – | – | – | – | – | ✓ |
| System Manager | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |

¹ write limited to their own sign-off row and their own assigned tasks — enforced by the
transition guard and by assignment, not by DocPerm alone.
² only the store openings within their `CH User Scope`.

**Permlevel-1 fields** (§16.9 "vendor and commercial confidentiality"): `budget_amount`,
`committed_cost`, `invoiced_cost`, `amount_paid`, `budget_variance` on `CH Store Opening`; and
`ch_budgeted_cost`, `ch_supplier` on `Task`. This is the mechanism that lets a task owner update
their task without seeing project financials — **standard Frappe, no custom code**.

## 17.5 Scope enforcement

Registered in `ch_projects/hooks.py`, delegating to `ch_erp15.ch_erp15.scope`:

```python
permission_query_conditions = {
    "CH Store Opening":          "ch_projects.scope.store_opening_query",
    "CH Store Opening Document": "ch_projects.scope.store_opening_document_query",
}
has_permission = {
    "CH Store Opening":          "ch_projects.scope.has_store_opening_permission",
    "CH Store Opening Document": "ch_projects.scope.has_store_opening_document_permission",
}
```

Scope resolves through the existing hierarchy **Company → City → Zone → Store**
(`CH User Scope`, 27 live rows, already registered for 20 doctypes). Before a store exists, a
proposal is scoped by **company + city**, both of which the proposal carries from Draft.

**Three enforcement rules this bench has learned the hard way:**

1. **One user-permission clause is ANDed per Link field.** A doctype with two `Link → Employee`
   fields becomes unsatisfiable under user permissions — this hit 8 `ch_hrms` doctypes.
   `CH Store Opening` deliberately types `project_manager` and `business_sponsor` as
   `Link → User`, not `Link → Employee`. If either is ever changed, it must be tested with a
   restricted user first.
2. **A `has_permission` hook returning falsy silently empties list views.** Every guard must be
   exercised as a non-bypass user in the test suite (report `20`, TC-27) — an import-only
   assertion has passed here before while the guard was unbound.
3. **Never let a scope filter loop call `frappe.throw`/`msgprint`.** Catching them does *not*
   clear `message_log`, so an N-row filter produces N popups. Use the non-raising
   `user_has_store_scope()` variant.

## 17.6 The `Branch` decision — required before Phase 3

`Branch` has **0 rows**, yet carries 6 ch_erp15 custom fields (`ch_company`, `ch_city`,
`ch_zone`, `ch_branch_address`, `ch_branch_address_display`, plus a section break) and a live
`branch_address_register` report. `CH Store.branch` links to it and is **NULL on all 56 stores**.
§16.1 asks for "proposed branch"; §16.12 asks to "create the Branch at the approved stage".

| Option | Consequence |
|---|---|
| **B1 — Make Branch real.** Provision a Branch at stage 9 for every new store **and back-fill all 56 existing stores in the same release.** | Branch becomes a usable dimension; `branch_address_register` starts returning rows; HRMS `Employee.branch` becomes meaningful. Cost: one back-fill patch and a naming decision (recommend `store_code`, matching the POS Profile and cost-centre convention). |
| **B2 — Retire Branch.** `CH Store` remains the sole store identity; drop `branch` from the proposal and deprecate `CH Store.branch`. | No second master to keep in sync. Cost: the 6 custom fields and the report become dead code and should be deleted, not left to mislead. |

**Recommendation: B1**, because HRMS ties `Employee` to `Branch` and §16.2 requires store
staffing and rosters. Under B2 there is no standard field linking an employee to their store,
which pushes the problem into `ch_hrms`'s `ch_store` links and leaves standard HR reports blind.

**What must not happen: creating Branches for new stores only.** That leaves 56 stores without
one and every Branch-dimensioned report silently wrong — the same shape of defect as the
phantom-Bin and dangling-reference incidents already recorded on this bench.

## 17.7 Notifications and escalation (§16.10)

Delivered through `ch_erp15.notification_router` (role×scope fan-out, company-scoped,
**fail-closed**) and `ch_erp15.sla_engine` (tiers at 🟡 warning / 🔴 breached / 🚨 critical on the
live `*/15 * * * *` cron). No new framework.

| Event | Recipients | Timing |
|---|---|---|
| New task assignment | assignee | immediate (Frappe assignment default) |
| Upcoming deadline | task owner | **daily digest** |
| Overdue task | task owner + department head | digest, promoted to immediate at 🔴 |
| Blocked task | PM + department head | immediate |
| **Critical-path delay** | PM + sponsor | immediate |
| Approval requested | approver | immediate |
| Approval rejected | requester | immediate |
| Missing completion evidence | task owner | **daily digest** |
| **Budget threshold exceeded** (80% / 100%) | PM + Finance | immediate |
| Delivery delayed | Procurement + PM | daily digest |
| Licence/document expiry | document owner + Compliance | digest at T-60/-30/-7; immediate at T-0 |
| **Opening date at risk** | PM + sponsor + Management | immediate |
| **Go-live approval required** | Management Approver | immediate |
| Project handover pending | Operations + PM | daily digest |

**Anti-flooding is a design rule, not a user setting.** Exactly five conditions notify
immediately by default (critical-path delay, budget threshold, opening date at risk, go-live
approval required, approval requested/rejected). Everything else lands in **one digest per
recipient per day**, produced by a scheduled job in the shape of
`ch_erp15/closure/scheduler.py`. Escalation *promotes* a digest item to immediate when its SLA
tier crosses 🔴 — it never sends both.

Escalation ladder for an overdue **critical** task: task owner → (24h) department head →
(48h) Project Manager → (72h) business sponsor + Management Approver. Thresholds live in
settings, not in code.

## 17.8 Audit

Two layers, both already available:

1. **Frappe `Version`** — field-level history on every doctype with `track_changes`. Already
   465,884 rows on this site; it works and needs no configuration beyond the flag.
2. **`CH Store Opening Audit Log`** (child table, modelled on `CH Closure Audit Log`) — one row
   per stage transition: `from_stage`, `to_stage`, `action`, `user`, `timestamp`, `reason`,
   `blocker_count_at_transition`, `readiness_percent_at_transition`.

Recording the readiness figures **at the moment of transition** is what makes a go-live decision
defensible six months later, once the underlying tasks have moved on. A live re-query cannot
reconstruct what the approver actually saw.
