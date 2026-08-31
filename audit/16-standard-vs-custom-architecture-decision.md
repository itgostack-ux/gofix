# 16 — Standard vs Custom: Architecture Decision

**Decision required before implementation** (§16.14).
**Inputs:** report `14` (measured capability assessment), report `15` (requirements matrix).
**Stack validated against:** Frappe **16.31.0**, ERPNext **16.32.3**, HRMS **16.16.0**, 15 installed
apps, 1,505 custom fields, 13 active workflows, **0 Projects and 0 Tasks**.

---

## 16.1 The options

| Option | Description |
|---|---|
| **A** | Configure standard ERPNext Project and Task functionality only |
| **B** | Extend standard Project/Task with custom fields, workflows and supporting DocTypes, maintained as site-level customisation or inside an existing app |
| **C** | Build a dedicated custom Frappe application integrated with standard ERPNext records |

## 16.2 Three findings that constrain the choice

**(1) Option A cannot carry the stage model — a controller conflict, not a preference.**
`Project.validate()` calls `update_percent_complete()` on **every save**
(`project.py:93-99`), and that method ends with:

```python
if self.status in ("Cancelled", "On hold"):
    return
self.status = "Completed" if self.percent_complete == 100 else "Open"
```

`Project.status` is a **derived** field with four options (Open / On hold / Completed /
Cancelled). An 18-stage `Workflow` on `Project` writes `workflow_state` and maps it into
`status`; the controller then overwrites `status` on the next save. Worse, the moment the last
task completes, the controller sets `status = "Completed"` — a store would be marked complete by
arithmetic, bypassing every go-live gate §16.5 requires. `Project` is also **not submittable**,
so there is no `docstatus` to approve or reject against.

The 18-stage lifecycle therefore **cannot** live on `Project` in any option. It needs its own
submittable record. That alone removes Option A from consideration for §16.5, §16.6 and §16.12.

**(2) A pre-approval record is structurally necessary.**
§16.16 requires testing "approval **and rejection**" of a store-opening proposal, and §16.13
requires preserving history "for comparing future store openings". A rejected proposal must
persist. If the proposal *is* the Project, every rejected site becomes a Project row that
pollutes `project_summary`, every `Budget`-against-Project figure, the Projects workspace and
`percent_complete` reporting. The proposal and the schedule are two different records with two
different lifetimes.

**(3) The expensive parts are already built — and they are in apps, not on the site.**
Report `14 §14.6` measured four in-house frameworks that cover the hardest requirements:
the closure **readiness engine** (registry-driven, `hard`-blocker aware, 14 live `CH Store`
checks, audit log, exception workflow); **`ch_store` provisioning** (idempotent store code,
cost-centre hierarchy, POS Profile, warehouse bins — proven across 56 stores);
**`CH Approval Authority` + `CH User Scope`** (26 and 27 live rows, already wired into
`permission_query_conditions` for 20 doctypes); and **`sla_engine` + `notification_router`**
(tiered escalation on a live `*/15` cron). Plus `CH Capex Request`, `CH Lease`,
`CH Workforce Plan Line` and `CH Onboarding Journey` — **all four of which already carry a
`ch_store` Link**.

None of these live as site-level customisation. Consuming them from site-level scripts (Option B's
weakest form) means Server Scripts importing app internals — untestable, unversioned, and invisible
to `bench migrate`.

## 16.3 Evaluation

Scored 1 (worst) – 5 (best) against the twelve §16.14 criteria. Every score carries its reason;
no score is a general impression.

| Criterion | A | B | C | Reasoning |
|---|:-:|:-:|:-:|---|
| **Functional coverage** | **1** | 4 | **5** | A cannot do: stage workflow (finding 1), evidence gates, task approvals, readiness blockers, critical path, date-revision history, master automation, document register. That is 8 of 13 requirement sections failed outright. B covers all of it functionally. C covers it and can additionally ship the readiness checks and provisioning orchestration as tested code. |
| **Upgrade safety** | **5** | 4 | **5** | A touches nothing. B adds ~22 custom fields on 6 standard doctypes — safe, but site-level Property Setters and Client Scripts silently survive an upgrade that changes the underlying field, and nobody finds out. C ships the same fields as **app fixtures**, so a `bench migrate` re-asserts them and a broken one fails loudly at migrate. No option patches ERPNext core. |
| **Maintainability** | 3 | **2** | **5** | A: nothing to maintain, but the work moves to spreadsheets. B is the weak point: site-level customisation is not in git, not code-reviewed, not testable, and does not deploy. **This bench has already been bitten** — report `05` found 2 custom fields (`Item.ch_default_warranty_plan`, `POS Profile.ch_petty_cash_section`) that existed **only in the local database** and in no repository. Putting a 13-section domain there repeats that at scale. C is one repo, one owner, one test suite. |
| **Data duplication** | 3 | 4 | **5** | A drives teams to spreadsheets — the worst duplication of all, and outside the system. B and C both read procurement, payment, stock, asset and employee data live via `project` / `CH Store` links. C scores higher only because a versioned codebase can *enforce* non-duplication in review; site-level scripts cannot. |
| **User experience** | 2 | 3 | **5** | A gives the Projects Team a generic Project form with no store fields, no readiness, no go/no-go. B improves the form but leaves the team navigating standard ERPNext list views. C ships the "New Store Projects" workspace, the readiness dashboard and the go/no-go panel as versioned assets. |
| **Reporting** | 2 | 4 | **5** | 5 standard Projects reports exist and none are store-aware. B can add Query Reports at site level (not versioned, not deployable). C ships the 17 reports of §16.11 as app code with tests. |
| **Workflow flexibility** | 2 | 4 | **5** | A: finding (1). B and C both put the workflow on a new submittable doctype; C additionally versions the transition **guards** (mandatory tasks, documents, budget, IT, finance validations) which §16.5 requires per transition and which a Workflow `condition` string cannot express safely. |
| **Security** | 3 | 4 | **5** | §16.9's "task owner must not see confidential financials" needs `permlevel = 1` — available in all three. But B's site-level scripts cannot be reviewed for the scope-leak class this bench has repeatedly hit (report `08` fixed 8 report-scope leaks; the Aug-24 auth rewrite missed 19 call sites). C puts the store-opening permission surface under the same test harness as `ch_erp15.scope`. |
| **Development effort** | **5** | 3 | **2** | A is configuration only. B is ~22 fields + 12 doctypes + scripts. C is the same content plus app scaffolding, fixtures, install/migrate hooks and tests. **C is the most expensive option and this is its only real cost.** |
| **Testing effort** | **5** | 3 | **2** | Same shape. But note the asymmetry: B's site-level artefacts are *hard* to test at all, so its lower effort partly reflects tests that will never be written. C's cost buys the §16.16 suite (29 scenarios, report `20`). |
| **Long-term scalability** | 2 | 3 | **5** | The estate is 56 stores across 4 companies with 815 cities configured. Store openings are a recurring, multi-year programme. B's site-level layer cannot be promoted between environments; every new site re-does it by hand. C installs. |
| **Integration** (procurement / assets / stock / accounts) | 4 | 4 | **5** | All three inherit `project` on MR/SQ/PO/PR/PI Items, `Budget.budget_against = Project` with a hard `Stop`, and `Stock Entry.project`. Only C can also *call* `ch_store.ensure_store_pos_profile()`, `ensure_store_cost_center_hierarchy()`, `ensure_store_bins()` and `accounts_setup.setup_company_accounts()` from a stage transition — an import a Server Script should never make. |
| **Total (/60)** | **37** | **42** | **54** | |

## 16.4 Recommendation

> **Option C — but scoped as tightly as Option B.**
>
> Build one new Frappe app, **`ch_projects`**, that keeps **ERPNext `Project` and `Task` as the
> schedule of record** and adds only the control layer ERPNext lacks: the store-opening proposal
> and its stage workflow, the readiness gate, the document register, the task-completion gate,
> the critical-path/date-revision engine, and the master-provisioning orchestration — every one
> of which **delegates to a framework that already exists on this bench**.

The recommendation matches the direction anticipated in §16.14, and the validation against the
code confirms it for a reason the brief could not have known: the reusable frameworks
(closure readiness, `ch_store` provisioning, approval authority, scope, SLA, notification router)
are already app-resident. Option C is not "build more" — it is "put the thin missing layer where
the thick existing layers already are".

**What makes this the smallest solution that satisfies the complete requirement:**

| Not built | Because it exists |
|---|---|
| Task engine, dependencies, Gantt, Kanban, timesheets | `Task` (51 fields, NestedSet, FS enforcement in `validate_status`) |
| Template → task generation with dependency remapping | `Project.copy_from_template()` |
| Budget enforcement on POs | `Budget.budget_against = Project`, `action_if_annual_budget_exceeded = Stop` |
| Procurement traceability | `project` on MR/SQ/PO/PR/PI Items |
| Readiness engine, hard blockers, audit log, exception path | `ch_erp15/closure/` |
| Store code, cost centres, POS Profile, warehouse bins, accounts | `ch_store.py` + `accounts_setup.py` |
| Approval authority and amount bands | `CH Approval Authority` |
| Company/city/zone/store permission scoping | `CH User Scope` + registered query conditions |
| SLA tiering and escalation | `ch_erp15.sla_engine` |
| Role×scope notification fan-out | `ch_erp15.notification_router` |
| Capex, lease, workforce plan, onboarding | `CH Capex Request`, `CH Lease`, `CH Workforce Plan Line`, `CH Onboarding Journey` — all already `ch_store`-linked |
| Document versioning primitive, file storage, audit history | `Version` (465,884 rows live), `File`, `Comment` |

Net new: **12 DocTypes, ~22 custom fields, 1 workflow, 1 workspace, 17 reports** (report `15 §15.14`).

## 16.5 Why a new app rather than a module inside `ch_erp15`

This is the one place the recommendation could reasonably differ, so the reasoning is explicit.

**Against `ch_erp15`:** it is already the bench's hub — **609 Python files**, importing from
`ch_pos` (24 sites), `ch_item_master` (19), `ch_logistics` (17) and `ch_mg_reports` (4). Report
`05` already flags that its `hooks.py` registers 5 handlers and 3 cron jobs **owned by other
apps**, and carries a `"*"` `doc_events` block running two guards on every doctype's validate.
Adding a thirteenth domain worsens a known, documented problem.

**For a new app — and the precedent is two apps old.** `ch_assets` and `ch_hrms`, the two most
recent additions, are both hard-dependent on `ch_erp15` and both deliberately **omit it from
`required_apps`**, asserting the dependency at install time instead. Their own comments give the
reason:

> `ch_erp15`'s `required_apps` names `ch_item_master`, `ch_pos`, `ch_payments` and `gofix` — and
> every one of those names `ch_erp15` straight back. Frappe resolves `required_apps` recursively
> **with no cycle detection**, so any new app that joins that graph recurses until the stack gives
> out (a `RecursionError` inside the redis wrapper, which is a misleading place to land).

`ch_projects` follows the same, proven pattern:

```python
required_apps = ["frappe", "erpnext"]          # stay out of the cycle
before_install = "ch_projects.setup.before_install"   # assert ch_erp15 / ch_item_master /
                                                      # ch_hrms / ch_assets with a real message
```

**Deployment consequence, stated plainly:** `ch_projects` will hold Link fields to `CH Store`
(`ch_item_master`), `CH Approval Authority` and `CH Role Link` (`ch_erp15`), `CH Capex Request`
and `CH Lease` (`ch_assets`), and will read `CH Workforce Plan Line` / `CH Onboarding Journey`
(`ch_hrms`). Those four apps must be present **before** `ch_projects` installs or migrates.
`before_install` enforces it; the §16.16 test plan (report `20`, TC-29) proves the failure is a
clear message and not a half-migrated site. This bench has already lost a site to a missing
dependency at `after_migrate` (`1146 tabCH Role Link doesn't exist` inside an `@atomic`
`post_schema_updates`) — hence report `15 §15.12`'s rule that provisioning commits per master
rather than in one atomic block.

## 16.6 Migration and rollout consequence

Because `Project`, `Task`, `Project Template`, `Task Type`, `Timesheet`, `Issue`, `Budget` and
`Asset` are **all at zero rows** (report `14 §14.3`), there is:

- **no data migration** — no existing projects to convert;
- **no behaviour regression risk** to a live user population — nobody uses the Projects module;
- **no conflict with existing customisation** — 0 CH custom fields on Project or Task today.

The only migration-shaped work is a **decision, not a script**: `Branch` has 0 rows but 6 CH
custom fields and a live report, and `CH Store.branch` is NULL on all 56 stores (report
`14 §14.7`). Creating Branches for new stores only would leave the estate half-populated and
every Branch-dimensioned report wrong. Report `17 §17.6` puts the two options to the business.

## 16.7 Recommended delivery sequence

| Phase | Content | Independently useful? |
|---|---|---|
| **0** | `ch_projects` app skeleton, `before_install` dependency assertion, `CH Store Format`, `CH Store Opening` + child tables, submit/cancel, roles, permlevel-1 financial fields | Yes — proposals can be raised, approved and rejected |
| **1** | `Project` creation on approval, `Project Template` + the §16.2 task library (report `19`), `CH Store Opening Requirement Rule`, `Task` custom fields, **server-side completion gate** | Yes — the schedule runs |
| **2** | Readiness checks registered against `ch_erp15/closure/readiness.py`, `CH Store Opening Readiness Section`, go/no-go panel, the 18-stage workflow + transition guards | Yes — go-live is controlled |
| **3** | Master provisioning orchestration (store code, accounts, cost centre, warehouse, POS Profile, Branch-pending-decision), `CH Store Opening Provision Log` with its unique index | Yes — openings become repeatable |
| **4** | Critical path + float, opening-date-at-risk, `CH Store Opening Date Revision`, SLA/escalation wiring | Yes — the schedule defends itself |
| **5** | `CH Store Opening Document` register, expiry/renewal cron, department sign-offs | Yes — compliance is provable |
| **6** | Workspace, 17 reports, stabilisation + `CH Store Opening Closure`, lessons-learned masters | Completes §16.11 and §16.13 |

Phases 0–2 deliver the requirement's core control value; each phase is shippable on its own.

## 16.8 Risks and how they are contained

| Risk | Containment |
|---|---|
| `required_apps` recursion on install | omit `ch_erp15` from `required_apps`; assert in `before_install` — the `ch_assets`/`ch_hrms` pattern |
| Half-migrated site if provisioning fails midway | commit per master, log each in `CH Store Opening Provision Log`; **no single `@atomic` block** |
| Duplicate Branch/Warehouse/Cost Center/POS Profile | delegate to the existing existence-checked `ch_store` functions; unique index on (`store_opening`, `action`) in the provision log |
| `Task.reschedule_dependent_tasks()` silently moving dates (§16.4 violation) | suppress for store-opening projects and route through `CH Store Opening Date Revision`; **avoid `override_doctype_class`** — this bench currently has zero such collisions across 8 apps and that is worth preserving |
| Custom fields drifting out of code | ship every field as an app fixture; a parity test asserts DB matches fixtures |
| Scope/permission leak | reuse `ch_erp15.scope`; test as a **non-bypass user** — an import-only assertion has passed before while the guard was unbound |
| Notification flooding | digest by default; only 4 conditions notify immediately (report `15 §15.10`) |
| Workspace not appearing after migrate | Workspace JSON is skipped unless its `modified` is newer than the DB row; ship forward-dated or force-import |

## 16.9 Decision summary

| | |
|---|---|
| **Recommended option** | **C** — dedicated `ch_projects` app on an ERPNext Project/Task foundation |
| **Score** | 54/60 vs B 42/60 vs A 37/60 |
| **Rejected: A** | cannot express the stage model (`Project.status` is controller-derived and `Project` is not submittable), and fails 8 of 13 requirement sections |
| **Rejected: B** | functionally adequate but unmaintainable at this scale; site-level customisation is unversioned and undeployable, and this bench has already lost custom fields to that exact failure |
| **Cost of the recommendation** | development and testing effort — the only two criteria where C loses |
| **What makes it small** | 12 new DocTypes and ~22 custom fields; every heavy subsystem delegates to an existing, proven CH framework |
| **Open decision for the business** | the `Branch` question (report `17 §17.6`) and confirmation of the 18 stages (§16.5 requires this explicitly) |
