# 14 — Existing ERPNext Project Capability Assessment

**Scope:** can the Projects Team's new-store-opening requirement (§16.1–16.13) be met by the
Projects module already installed on this bench?
**Method:** every count and field name below was read from the working tree or queried against
the live database. Nothing is inferred from documentation.

---

## 14.1 Measured environment

| Item | Measured value |
|---|---|
| Frappe | **16.31.0** (branch `version-16`) |
| ERPNext | **16.32.3** (branch `version-16`) |
| HRMS | **16.16.0** |
| Site / DB | `erpnext.local` / `_7e1ff8b754d64fee` (MariaDB) |
| Installed apps | 15 — `frappe, erpnext, hrms, india_compliance, ch_item_master, gofix, buyback, ch_pos, ch_payments, ch_erp15, ch_mg_reports, ch_logistics, insights, ch_assets, ch_hrms` |

> **Correction to `01-application-architecture.md`.** That document records the stack as
> "Frappe/ERPNext v15-line". It is **v16** on all three core apps. Any v15 assumption in the
> earlier audit files should be re-checked; this document uses the measured v16 source.

## 14.2 What the Projects module ships

`apps/erpnext/erpnext/projects/` — **15 DocTypes, 5 reports, 1 workspace, 1 dashboard**.

| DocType | Role |
|---|---|
| `Project` | 59 fields, **not submittable**, `naming_series: PROJ-.####` |
| `Task` | 51 fields, **not submittable**, NestedSet (`lft`/`rgt`/`parent_task`) |
| `Task Depends On` | child table — finish-to-start predecessor list |
| `Project Template` / `Project Template Task` | template header + `Link → Task` rows (template tasks are `Task` rows with `is_template=1`) |
| `Project Type`, `Task Type` | classification masters |
| `Project Update` | periodic status snapshot (`collect_progress` cron) |
| `Project User` | per-project user list, drives portal access |
| `Timesheet` / `Timesheet Detail` | effort + costing |
| `Activity Type` / `Activity Cost` | billing/costing rates |
| `Projects Settings` | module settings |
| `Dependent Task` | (template dependency helper) |

Reports: `project_summary`, `delayed_tasks_summary`, `daily_timesheet_summary`,
`timesheet_billing_summary`, `project_wise_stock_tracking`.

## 14.3 Measured usage on this site: **zero**

```
Project 0 | Task 0 | Project Template 0 | Task Type 0 | Timesheet 0 | Issue 0
Project Type 3 (ERPNext install defaults)
```

Custom fields on the module: **2 in total**, both from HRMS
(`Project.total_expense_claim`, `Task.total_expense_claim`) — plus `Timesheet.salary_slip`.
Out of **1,505 Custom Fields** on this site, **not one** is a CH field on `Project` or `Task`.

**Conclusion: the Projects module is installed, untouched and unused.** There is no legacy
configuration to preserve and no migration of existing project data to perform. This is a
greenfield adoption, which materially lowers the risk of Option B/C in report `16`.

## 14.4 The store estate the solution must plug into (measured)

| Master | Rows | Note |
|---|---|---|
| `CH Store` | **56** — 53 Active, 2 Closed, **1 Planned** | `store_status` already carries a `Planned` state |
| `Warehouse` | 431 | provisioned per store by `ch_store.ensure_store_bins` |
| `POS Profile` | 56 | provisioned by `ch_store.ensure_store_pos_profile` |
| `Cost Center` | 204 | provisioned by `ch_store.ensure_store_cost_center_hierarchy` |
| `Company` | 4 | |
| `Department` | 53 | |
| `CH City` / `CH State` / `CH Store Zone` | 815 / 38 / 9 | the Region→State→City→Zone spine §16.1 asks for |
| `Brand` | 174 | |
| `Branch` | **0** | see §14.7 |
| `Budget` | **0** | |
| `Asset` | **0** | |
| `Employee` | 7 | |

`CH Store.opening_date` exists and is **NULL on every one of the 56 rows** — the store-opening
date has never been captured anywhere in this system.

## 14.5 Capability-by-capability assessment

### Covered correctly by standard ERPNext — reuse, do not rebuild

| Requirement | Standard mechanism (verified in source) |
|---|---|
| §16.2 template → task generation | `Project.copy_from_template()` (`project.py:103`) clones template `Task` rows on insert |
| §16.2 template preserves structure | `dependency_mapping` / `check_depends_on_value` / `check_for_parent_tasks` re-point `depends_on` and `parent_task` onto the newly created tasks |
| §16.4 finish-to-start enforcement | `Task.validate_status()` (`task.py:139`) **throws** if you complete a task whose `depends_on` rows are not Completed/Cancelled |
| §16.3 task hierarchy / workstream grouping | `Task` is a NestedSet — a group Task per workstream, children beneath |
| §16.3 owner / supporting members | `ToDo` via `_assign` (Frappe assignment); `Task.completed_by`; `close_all_assignments` fires on completion |
| §16.3 department | `Task.department` → `Department` (53 rows exist) |
| §16.3 planned/actual dates + effort | `exp_start_date`/`exp_end_date` (**Datetime**), `act_start_date`/`act_end_date` (Date), `expected_time`, `actual_time` |
| §16.3 priority, status, % complete, milestone | `priority`, `status`, `progress`, **`is_milestone`** |
| §16.3 comments / attachments / audit | Frappe `Comment`, `File`, `Version` (465,884 rows on this site — versioning is live and working) |
| §16.6 overall % | `Project.percent_complete_method` = Manual / Task Completion / Task Progress / **Task Weight** |
| §16.7 budget enforcement | **`Budget.budget_against` supports `Project`**, with `applicable_on_material_request`, `applicable_on_purchase_order`, `applicable_on_booking_actual_expenses` and `action_if_annual_budget_exceeded` = **Stop**/Warn/Ignore |
| §16.7 procurement traceability | a `project` Link field already exists on `Material Request Item`, `Supplier Quotation Item`, `Purchase Order Item`, `Purchase Receipt Item`, `Purchase Invoice Item`, `Stock Entry` |
| §16.7 cost rollup | `Project.total_purchase_cost`, `total_costing_amount`, `total_consumed_material_cost`, `estimated_costing`, `cost_center` |
| §16.10 notification plumbing | Frappe `Notification` (29 configured on this site), Email/System channels |
| §16.11 workspace/report shell | `Workspace`, `Number Card`, `Dashboard Chart`, Query/Script Report |
| §16.5 stage workflow | Frappe `Workflow` — 13 active on this site, incl. two with `override_status=1` |

### Hard gaps — nothing in ERPNext v16 does these

| # | Gap | Evidence |
|---|---|---|
| G1 | **No critical path.** | `grep -rn "critical_path\|critical path" apps/erpnext/erpnext/projects/` → **0 hits** |
| G2 | **Date changes are silent and unlogged.** `Task.reschedule_dependent_tasks()` (`task.py:270`) shifts successor `exp_start_date`/`exp_end_date` and calls `task.save()` with no reason, no previous-date record and no approval. §16.4 explicitly forbids this. It also only touches successors whose `status == "Open"` and whose start precedes the new end — a `Working` successor is silently **not** rescheduled, so the plan quietly desynchronises. | source read |
| G3 | **No project-level impact.** The reschedule never touches `Project.expected_end_date`, so a slipped task cannot raise "opening date at risk". | source read |
| G4 | **No mandatory / evidence / checklist / approval on `Task`.** The 51 fields contain no `is_mandatory`, `requires_evidence`, `checklist`, `approver`, `approved_by`, `rejection_reason`, `blocked`, `blocked_reason`, `risk_level`, `sla`, `budgeted_cost`, `vendor`. §16.3 requires all of them. | field dump |
| G5 | **Task completion is not gated on evidence.** `validate_status` checks predecessors only. A task can be Completed with zero attachments. | source read |
| G6 | **`percent_complete` is a flat average.** All four methods average over *every* task equally (`update_percent_complete`, `project.py:214`). A project with 100 trivial tasks done and the fire-NOC task open reads high. §16.6 explicitly forbids this. Task Weight helps but still cannot *block*. | source read |
| G7 | **No readiness/gate concept on `Project`.** Nothing prevents `status = Completed` while mandatory tasks are open — status is *derived* from percent_complete. | source read |
| G8 | **`Project` has no store dimension.** No `branch`, `city`, `state`, `zone`, `store`, `store_format`, `brand`, `latitude`/`longitude`, `warehouse`, `pos_profile`, `sponsor`, `opening_date` fields. Only `customer`, `sales_order`, `department`, `cost_center`, `company`. | 59-field dump |
| G9 | **`Asset` has no `project` field.** Confirmed: Asset's links are `item_code, company, location, custodian, cost_center, department, purchase_receipt, purchase_invoice`. §16.7 "no asset creation without project/store mapping" is unenforceable today. | field dump |
| G10 | **`Request for Quotation` (header) has no `project` field**, so the RFQ step in the §16.7 chain breaks traceability between MR and Supplier Quotation. | field dump |
| G11 | **No document register.** Frappe `File` has no `document_type`, `version`, `valid_from`, `expiry_date`, `approval_status`, `approved_by`. §16.8 needs all of them. | field dump |
| G12 | **No master-creation automation from a project.** Branch/Warehouse/Cost Center/POS Profile creation exists (see §14.6) but is triggered by saving a `CH Store`, not by reaching a project stage. | source read |
| G13 | **No SLA / time-remaining on `Task`.** ERPNext's SLA (`Service Level Agreement`) is bound to Issue-style doctypes, not Task. | module read |
| G14 | **`Assignment Rule`: 0 configured.** The doctype exists; region/department-based auto-assignment (§16.12) is unconfigured, not unavailable. | DB count |

## 14.6 Already built in-house — the decisive finding

Three CH frameworks already solve, for the *closing* and *staffing* directions, exactly the
problems §16.5/16.6/16.8/16.12 pose for the *opening* direction. **Any new build must extend
these, not parallel them.**

**(a) Entity Closure Framework — `ch_erp15/closure/`** (`readiness.py`, `checks/`, `guards.py`,
`transitions.py`, `audit.py`, `scheduler.py`, `api.py`, `exceptions.py`)

- `ReadinessResult(status, count, details, resolve_route, severity)` and
  `CheckSpec(check_code, area, category, fn, hard, severity)`, a per-entity-type registry, and
  `run_readiness()` which isolates a failing check so one bug cannot poison the report.
- `CH Closure Request` (submittable, `CLOSE-{entity_type_short}-{YY}-{####}`) carries
  `readiness_score`, `pass_count`, `warning_count`, **`blocker_count`**, a
  `CH Closure Readiness Item` result table, an actor block
  (requested/approved/closed/reopened by+on) and a `CH Closure Audit Log` child table.
- Two live workflows (`CH Closure Request Workflow`, `CH Closure Exception Request Workflow`),
  both with `override_status = 1`.
- **14 registered `CH Store` checks** today (POS sessions, settlements, cash drops, closing
  vouchers, kiosk tokens, manifests, buyback orders, repair intake, linked warehouse, POS
  profile pointing, user mappings, devices, open invoices).

This is a **hard-blocker-aware readiness engine with an audit trail and an exception path** —
precisely the §16.6 requirement ("the readiness score must not be a misleading average;
mandatory critical tasks should have blocking behaviour regardless of the overall percentage"),
already written, already tested, already in production use.

**(b) Store master provisioning — `ch_item_master/ch_core/doctype/ch_store/ch_store.py`** (908 lines)

Idempotent, existence-checked provisioning already exists for every master §16.12 lists:

| Function | Creates |
|---|---|
| `_generate_prefixed_store_code()` / `_generate_store_code()` | **store code**, from `Company.store_code_prefix`, with `next_free_numeric_id("store")` for `store_id` |
| `ensure_store_cost_center_hierarchy()` / `_ensure_cost_center_node()` | region + store **Cost Center** nodes |
| `ensure_store_pos_profile()` / `_create_store_pos_profile_with_permissions()` | **POS Profile** named `POS - {store_code}`, with permissions |
| `assign_pos_profile_cost_center()` | binds profile → cost centre without repointing an explicit assignment |
| `ensure_store_bins()` | the store's **Warehouse** bin set |
| `backfill_store_pos_profiles()` | retro-fit for existing stores |
| `accounts_setup._ensure_store_cost_centers()` | company-wide sweep; explicitly documented as converging seed/backfill and runtime creation |

`accounts_setup.py` (1,063 lines) additionally provides `_ensure_account`,
`_ensure_default_accounts`, `_set_company_defaults`, `_wire_mop_accounts`, `_map_tds_accounts`,
`_ensure_payment_modes`, `setup_company_accounts` — the **accounts** half of "stores to create
new store codes and accounts".

The duplicate-prevention §16.12 demands is therefore **already implemented and proven** across
56 stores. The missing piece is *when* it fires: today on `CH Store` save; the requirement is
at a controlled, approved project stage.

**(c) ch_hrms — the people half.** Already links to `CH Store` **by Link field**:

| DocType | Store link | Use for store opening |
|---|---|---|
| `CH Workforce Plan` + `CH Workforce Plan Line` | `ch_store` | §16.2 manpower planning — `budgeted_headcount`, `open_positions`, `planned_hires`, `gap`, `avg_ctc`, `budgeted_cost` |
| `CH Onboarding Journey` + `CH Onboarding Task` | `ch_store` | §16.2 employee onboarding, per-hire |
| `CH Onboarding Journey Template` + `CH Onboarding Template Task` | — | template→instance pattern with `due_offset_days` |
| `CH Candidate`, `CH Hiring Stage`, `CH Offer Approval`, `CH Interview Scorecard` | — | §16.2 recruitment |
| `CH Training Session`, `CH Course`, `CH Course Enrollment`, `CH Learning Path` | — | §16.2 SOP / cash-handling / product / repair training |
| `CH Employee Document` + `CH Employee Document Type` | — | §16.8 document-register pattern to copy verbatim |
| `CH Shift Coverage Plan` / `Slot` | — | §16.2 rosters, opening-day staffing |

**`CH Onboarding Task` is the single most important precedent in this codebase.** Its fields are
`task, category, assigned_to, owner_role, due_date, status, ` **`is_mandatory`**, **`blocks_activation`**,
**`requires_evidence`**, `document_type`, **`evidence` (Attach)**, `completed_on`, `completed_by`,
`instructions`, `remarks`. That is G4 and G5 — the exact gap in ERPNext `Task` — **already solved
once inside this bench**, with a template counterpart carrying `due_offset_days`. The store-opening
task model should be the same shape so the two read alike to the same users.

**(d) Other reusable in-house parts**

| Asset | Location | Serves |
|---|---|---|
| `CH Capex Request` (submittable) | `ch_assets` | §16.2 capex estimate, §16.7 — already links `material_request`, `purchase_order`, `created_asset`, `budget_account`, `budget_available`, `required_approver_role` |
| `CH Lease` (submittable, Ind AS 116) | `ch_assets` | §16.2/16.8 lease agreement, `security_deposit`, `commencement_date`, `lease_term_months` — **already has a `ch_store` Link** |
| `CH Approval Authority` (26 rows) | `ch_erp15` | §16.9 approval authority: `doctype_target`, `action`, `role`, `company`, `min_amount`/`max_amount`, `priority`, `condition` |
| `CH User Scope` (27 rows) + `CH Role Link` | `ch_erp15` | §16.9 company/city/zone/store scoping, with `permission_query_conditions` + `has_permission` already registered for 20 doctypes |
| `ch_erp15.sla_engine` | `ch_erp15` | §16.3 SLA / §16.10 escalation — `compute_tier`, `_tier_from_window`, tiered 🟡/🔴/🚨 notification, on a `*/15 * * * *` cron |
| `ch_erp15.notification_router` + `CH Notification Settings` | `ch_erp15` | §16.10 role×scope fan-out, company-scoped, fail-closed |
| `ch_erp15/closure/scheduler.py` | `ch_erp15` | §16.10 consolidated (non-flooding) reminder cadence |

## 14.7 A pre-existing defect this project must decide on: `Branch`

`Branch` has **0 rows**, yet ch_erp15 has already extended it with 6 custom fields
(`ch_company`, `ch_city`, `ch_zone`, `ch_branch_address`, `ch_branch_address_display`, plus
section breaks) and ships a `branch_address_register` report. `CH Store.branch` is a Link to
`Branch` and is **NULL on all 56 stores**.

So the "proposed branch" in §16.1 targets a master that is designed, extended, reported on —
and never populated. §16.12's "Create the Branch at the approved stage" is the first thing that
would ever write to it. This needs a business decision (report `17`, §17.6): either Branch
becomes real and back-fills for the 56 existing stores, or `CH Store` stays the sole store
identity and Branch is retired. **Do not create Branch rows for new stores only** — that leaves
the estate half-populated and every Branch-dimensioned report wrong.

## 14.8 Verdict

Standard ERPNext v16 Project/Task covers the **schedule and procurement spine** of this
requirement well: templates, hierarchy, finish-to-start enforcement, assignment, effort,
project-dimensioned budget with a hard `Stop`, and `project` traceability through
MR→SQ→PO→PR→PI. It does **not** cover the control layer the Projects Team actually needs:
mandatory tasks, evidence gates, approvals, readiness scoring with hard blockers, opening-date
risk, auditable rescheduling, a document register, or controlled master creation.

Every one of those missing pieces has a **working in-house precedent** — the closure readiness
engine, `CH Onboarding Task`, `ch_store` provisioning, `CH Approval Authority`, `CH User Scope`,
`sla_engine`. The correct shape of the solution is therefore not "build a project system" and
not "configure ERPNext and hope"; it is **ERPNext Project/Task as the schedule of record, plus a
thin control layer that reuses the CH frameworks already proven on this bench.**

Detailed requirement-by-requirement mapping: report `15`. Option scoring and recommendation:
report `16`.
