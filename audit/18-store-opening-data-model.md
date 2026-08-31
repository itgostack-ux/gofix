# 18 — Store Opening Data Model

Data model for the `ch_projects` app recommended in report `16`. Covers every element §16.15
requires: standard DocTypes reused, standard DocTypes extended, custom fields added, new
DocTypes and child tables proposed, links to procurement/assets/stock/accounting, source of
truth per field, duplicate-data risks, migration, indexing and the permission model.

**Principle:** `CH Store Opening` is the **control record**; `Project`/`Task` are the **schedule
of record**; ERPNext transactions are the **financial and physical record**. No figure exists in
two of the three.

---

## 18.1 Entity map

```
                       ┌──────────────────────────────┐
                       │      CH Store Opening        │  (new, submittable)
                       │  SO-{YY}{MM}-{####}          │
                       └──────────────┬───────────────┘
        ┌───────────────┬─────────────┼──────────────┬─────────────────┐
        │ 1:1           │ 1:1         │ 1:N          │ 1:N             │ 1:N
        ▼               ▼             ▼              ▼                 ▼
   ┌─────────┐   ┌────────────┐  ┌──────────┐  ┌────────────┐  ┌───────────────┐
   │ Project │   │  CH Store  │  │ CH Store │  │  CH Store  │  │   CH Store    │
   │  (std)  │   │ (existing) │  │ Opening  │  │  Opening   │  │   Opening     │
   └────┬────┘   └─────┬──────┘  │ Document │  │ Provision  │  │   Closure     │
        │ 1:N          │         └──────────┘  │    Log     │  └───────────────┘
        ▼              │                        └────────────┘
   ┌─────────┐         │  provisions (idempotent, via ch_store.py / accounts_setup.py)
   │  Task   │         ├──► Warehouse ──► Bin
   │  (std)  │         ├──► Cost Center ──► Account
   └────┬────┘         ├──► POS Profile
        │              ├──► Branch  (pending decision — report 17 §17.6)
        │              └──► store_code + store_id
        │
        │ referenced by (project / ch_project link)
        ├──► Material Request ─► RFQ ─► Supplier Quotation ─► Purchase Order
        │                                     └─► Purchase Receipt ─► Purchase Invoice ─► Payment Entry
        ├──► Stock Entry
        ├──► Asset ─► Asset Movement
        ├──► Budget (budget_against = Project)
        ├──► Timesheet
        └──► Issue            (stabilisation)

   ch_hrms (already ch_store-linked, no new links needed):
        CH Workforce Plan Line ─┐
        CH Onboarding Journey ──┼──► CH Store
        CH Shift Coverage Plan ─┘
   ch_assets:
        CH Capex Request ─┐
        CH Lease ─────────┴──► CH Store  (+ new ch_store_opening back-link)
```

## 18.2 Standard DocTypes reused, unmodified

| DocType | Role in the model | Owner of truth for |
|---|---|---|
| `Project` | schedule of record, cost rollup, budget dimension | `percent_complete`, `total_purchase_cost`, `actual_start_date`, `actual_end_date`, `estimated_costing` |
| `Task` | the work | schedule, dependencies, effort, progress |
| `Task Depends On` | finish-to-start predecessors | dependency graph |
| `Project Template` / `Project Template Task` | reusable task library | template structure |
| `Task Type` | workstream classification | — |
| `Project Type` | "New Store Opening" | — |
| `Project User` | portal/notification audience | — |
| `Timesheet` / `Timesheet Detail` | actual effort and labour cost | `actual_time` |
| `ToDo` | task assignment (owner + supporting members) | assignment |
| `Budget` | approved budget, **`budget_against = Project`** | approved budget, overrun action |
| `Material Request` / `Item` | requirement | requested qty |
| `Supplier Quotation` / `Item` | quotation comparison | quoted rate |
| `Purchase Order` / `Item` | commitment | committed cost |
| `Purchase Receipt` / `Item` | receipt | received qty, receipt date |
| `Purchase Invoice` / `Item` | supplier invoice | invoiced cost |
| `Payment Entry` / `Payment Entry Reference` | payment | amount paid |
| `Stock Entry` | opening stock transfer | quantities moved |
| `Warehouse`, `Bin` | store stock location | stock on hand |
| `Cost Center`, `Account`, `GL Entry` | accounting | actual GL cost |
| `Asset`, `Asset Movement` | capital items | asset value, custody |
| `Supplier` | vendor | vendor master |
| `Employee`, `Department`, `Designation` | people | employee master |
| `Issue`, `Issue Type` | stabilisation problems | issue record |
| `Address`, `File`, `Comment`, `Version` | address, attachments, discussion, audit | — |
| `Workflow`, `Notification`, `Assignment Rule` | control and comms | — |
| `Brand`, `Territory` | classification | — |

## 18.3 Existing CH DocTypes reused, unmodified

| DocType | App | Role |
|---|---|---|
| `CH Store` | ch_item_master | the store master, **created at stage 9** |
| `CH City` / `CH State` / `CH Store Zone` | ch_item_master | location spine (815 / 38 / 9 rows) |
| `CH Approval Authority` | ch_erp15 | approval routing and amount bands (26 rows) |
| `CH User Scope` (+ Company/City/Zone/Store children) | ch_erp15 | permission scope (27 rows) |
| `CH Role Link` | ch_erp15 | role tables on settings doctypes |
| `CH Closure Readiness Item` pattern | ch_erp15 | shape of the readiness result row |
| `CH Workforce Plan` / `CH Workforce Plan Line` | ch_hrms | **manpower planning per store** — already `ch_store`-linked |
| `CH Onboarding Journey` / `CH Onboarding Task` | ch_hrms | **employee onboarding per store** — already `ch_store`-linked |
| `CH Shift Coverage Plan` / `Slot` | ch_hrms | rosters, opening-day staffing |
| `CH Candidate`, `CH Hiring Stage`, `CH Offer Approval` | ch_hrms | recruitment |
| `CH Training Session`, `CH Course Enrollment` | ch_hrms | SOP / cash-handling / product / repair training |
| `CH Employee Document` / `Type` | ch_hrms | pattern copied by `CH Store Opening Document` |

### 18.3.1 The ch_hrms link, in detail

Staffing a new store is **not** modelled inside the project. The project holds *tasks*; ch_hrms
holds the *people records*, and both already point at `CH Store`.

| §16.2 People & Operations task | Resolves to | Link |
|---|---|---|
| Manpower planning | `CH Workforce Plan Line` rows for the store | `ch_store` (existing) |
| Recruitment request | `CH Candidate` + `CH Hiring Stage` | via department/designation |
| Store manager appointment | `Employee` + `CH Offer Approval` | `Employee.branch` (pending §17.6) |
| Staff recruitment | `CH Candidate` | — |
| Employee onboarding | `CH Onboarding Journey` per hire, from `CH Onboarding Journey Template` | `ch_store` (existing) |
| Training / SOP / cash handling / product / repair | `CH Training Session` + `CH Course Enrollment` | — |
| Attendance setup | HRMS `Shift Type` + attendance device (IT workstream) | — |
| Rosters, opening-day staffing | `CH Shift Coverage Plan` | `ch_store` (existing) |
| Uniforms | procurement task → MR/PO | `project` |

**Two staffing readiness checks** consume these directly, so the project never restates headcount:

- `SO_STAFF_HEADCOUNT` — `hard=True`: `Employee` count for the store ≥ `budgeted_headcount`
  summed from `CH Workforce Plan Line` where `ch_store = <store>`.
- `SO_STAFF_ONBOARDING` — `hard=True`: no `CH Onboarding Journey` for the store with an
  incomplete task where `blocks_activation = 1`.

The second reuses ch_hrms's own `blocks_activation` flag as-is. Headcount, joining dates and
training completion have exactly **one** home, in ch_hrms.

**Task assignment to people** uses Frappe assignment (`ToDo` / `_assign`) against a `User`, with
`Task.department` for the department dimension. `Employee` is *not* linked from `Task`:
`Task.completed_by` is already a `Link → User`, a second Employee link would create two
unreconciled owner fields, and — per report `17 §17.5` — a second `Link → Employee` on a doctype
is the exact shape that made 8 `ch_hrms` doctypes unreadable under user permissions.
`Employee.user_id` is the bridge where an Employee record is genuinely needed.

## 18.4 Standard DocTypes extended (custom fields)

All fields ship as **app fixtures** in `ch_projects`, never created through the UI.
Prefix `ch_`, matching `Asset.ch_asset_tag` and `Branch.ch_city`.

### `Task` — 21 fields

| Field | Type | Options / note | permlevel |
|---|---|---|:-:|
| `ch_store_opening` | Link | `CH Store Opening` | 0 |
| `ch_workstream` | Select | 9 workstreams of §16.2 (denormalised from the group task for reporting) | 0 |
| `ch_is_mandatory` | Check | blocks go-live if incomplete | 0 |
| `ch_is_critical_path` | Check | **computed, read-only** | 0 |
| `ch_total_float` | Float | days of slack, **computed, read-only** | 0 |
| `ch_sla_hours` | Int | from the template task | 0 |
| `ch_sla_target` | Datetime | **computed** from actual start + SLA | 0 |
| `ch_blocked` | Check | | 0 |
| `ch_blocked_reason` | Small Text | mandatory when `ch_blocked` | 0 |
| `ch_blocked_since` | Datetime | **set by the controller**, not the user | 0 |
| `ch_risk_level` | Select | Low / Medium / High / Critical | 0 |
| `ch_requires_evidence` | Check | | 0 |
| `ch_checklist` | Table | `CH Task Checklist Item` | 0 |
| `ch_approver` | Link | User | 0 |
| `ch_approval_status` | Select | Not Required / Pending / Approved / Rejected | 0 |
| `ch_approved_by` | Link | User, read-only | 0 |
| `ch_approved_on` | Datetime | read-only | 0 |
| `ch_rejection_reason` | Small Text | | 0 |
| `ch_material_request` | Link | Material Request | 0 |
| `ch_asset` | Link | Asset | 0 |
| `ch_budgeted_cost` | Currency | | **1** |
| `ch_supplier` | Link | Supplier | **1** |

Deliberately **absent**: actual cost, committed cost, PO number, PI number, payment status,
time remaining, overdue flag. All derived at read time (§18.7).

### Other standard DocTypes

| DocType | Field | Type | Why |
|---|---|---|---|
| `Project` | `ch_store_opening` | Link → CH Store Opening | back-link |
| `Project` | `ch_store` | Link → CH Store | set at stage 9 |
| `Asset` | `ch_project` | Link → Project | **Gap G9** — Asset has no project field |
| `Asset` | `ch_store` | Link → CH Store | §16.7 "no asset without project/store mapping" |
| `Request for Quotation` | `ch_project` | Link → Project | **Gap G10** — RFQ header has no project field |
| `Issue` | `ch_store_opening` | Link → CH Store Opening | stabilisation issues |
| `CH Store` | `ch_store_opening` | Link → CH Store Opening | provenance back-link |
| `CH Store` | `store_format` | Link → CH Store Format | §16.1 store format |
| `CH Capex Request` | `ch_store_opening` | Link → CH Store Opening | §16.7 capex traceability |
| `CH Lease` | `ch_store_opening` | Link → CH Store Opening | §16.8 lease traceability |

`Material Request.custom_store` and `Purchase Order.custom_target_store` (both `Link → CH Store`)
**already exist** and are reused — no second store field is added to either.

## 18.5 New DocTypes

### 1. `CH Store Opening` — master, submittable
`autoname: format:SO-{YY}{MM}-{####}` · `track_changes: 1` · `title_field: proposed_store_name`

| Group | Fields |
|---|---|
| Identity | `proposed_store_name`, `brand`, `company`, `store_format`, `store_status_note` |
| Location | `zone`, `state`, `city`, `address`, `latitude`, `longitude`, `branch` |
| People | `project_manager`, `business_sponsor`, `members` (Table) |
| Dates | `proposed_opening_datetime`, `approved_opening_datetime` (RO), `stabilisation_days`, `handover_date` (RO), `closure_date` (RO) |
| Stage | `stage` (Select ×18), `workflow_state`, `on_hold_reason`, `rejection_reason`, `cancellation_reason` |
| Links (RO) | `project`, `ch_store`, `warehouse`, `cost_center`, `pos_profile` |
| Financial **(permlevel 1)** | `budget_amount`, `committed_cost`, `invoiced_cost`, `amount_paid`, `budget_variance`, `budget_variance_percent` |
| Readiness (RO) | `readiness_percent`, `blocker_count`, `warning_count`, `pass_count`, `readiness_run_on`, `project_health`, `risk_status`, `opening_date_at_risk`, `go_no_go` |
| Children | `members`, `signoffs`, `date_revisions`, `audit_log` |
| Standard | `amended_from` |

### 2. `CH Store Opening Document` — master
`autoname: format:SOD-{store_opening}-{####}`
`store_opening`, `document_type`, `document_number`, `version` (Int), `is_current` (Check),
`supersedes` (Link, self), `valid_from`, `expiry_date`, `days_to_expiry` (Int, computed),
`approval_status` (Pending/Verified/Rejected/Expired), `verified_by`, `verified_on`,
`rejection_reason`, `task` (Link → Task), `requires_renewal`, `renewal_lead_days`,
`document_file` (Attach), `is_private`, `remarks`.

### 3. `CH Store Opening Document Type` — master
`autoname: field:document_type_name` · `category` (Legal/Statutory/Licence/Design/Financial/
Asset/Insurance/Photograph/Test Result/Signoff/Handover), `applies_to_state`, `applies_to_brand`,
`applies_to_store_format`, `is_statutory`, `blocks_go_live`, `default_validity_months`,
`requires_renewal`, `owner_role`, `approver_role`.

### 4. `CH Store Opening Requirement Rule` — master
`rule_name`, `is_active`, `company`, `applies_to_state`, `applies_to_city`, `applies_to_brand`,
`applies_to_store_format`, `requirement_category`, `template_task` (Link → Task, `is_template=1`),
`document_type` (Link), `is_mandatory`, `blocks_go_live`, `priority` (Int), `condition` (Small Text).
Solves §16.2's "configurable by state, city, brand and store type; do not hardcode one
location's licence requirements".

### 5. `CH Store Opening Readiness Section` — master
`section`, `company`, `owner_role`, `approver_role`, `weight` (Float), `is_blocking` (Check),
`display_order` (Int). Supplies §16.6's "each section must have an accountable owner and
approver" as data.

### 6. `CH Store Opening Provision Log` — master
`store_opening`, `action` (Select: Store Code / Accounts / Cost Center / Warehouse / POS Profile /
Branch / User Access / Opening Stock / Store Locator), `status` (Pending/Success/Failed/Skipped),
`created_doctype`, `created_document`, `error` (Text), `run_by`, `run_on`, `attempt` (Int).
**Unique index on (`store_opening`, `action`)** — this is what makes §16.12's idempotency,
retry-safety and duplicate-master prevention structural rather than conventional.

### 7. `CH Store Opening Closure` — master, submittable
`store_opening`, `closure_date`, `budget_reconciled` (Check), `final_cost`, `variance`,
`variance_explanation`, `open_procurement_count`, `asset_count`, `asset_reconciled` (Check),
`document_completeness_percent`, `what_went_well`, `what_delayed_us`,
`delay_causes` (Table MultiSelect → `CH Store Opening Delay Cause`), `lead_time_days` (Int),
`approved_by`, `approved_on`.

### 8. `CH Store Format` — master
`format_name`, `description`, `min_area_sqft`, `max_area_sqft`, `default_project_template`
(Link → Project Template), `default_headcount`, `is_active`.

### 9. `CH Store Opening Delay Cause` — master
`cause_name`, `category` (Property/Legal/Design/Procurement/IT/HR/Finance/Vendor/External),
`description`. A **master link, not free text** — this is what makes §16.11's "repeated causes
of delay" report a `GROUP BY` rather than a text-mining exercise.

## 18.6 New child tables

| Child table | Parent | Fields |
|---|---|---|
| `CH Store Opening Member` | CH Store Opening | `user`, `full_name` (fetch), `role_in_project`, `department`, `is_department_owner`, `notify` |
| `CH Store Opening Signoff` | CH Store Opening | `section`, `department`, `approver_role`, `approver` (Link → User), `status` (Pending/Approved/Rejected), `signed_on`, `remarks` |
| `CH Store Opening Audit Log` | CH Store Opening | `timestamp`, `user`, `action`, `from_stage`, `to_stage`, `reason`, `blocker_count_at_transition`, `readiness_percent_at_transition` |
| `CH Store Opening Date Revision` | CH Store Opening | `revision_datetime`, `revised_by`, `field_revised` (Opening Date / Task End Date), `task` (Link), `previous_datetime`, `revised_datetime`, `slip_days` (Int), `reason`, `is_committed_date`, `approved_by`, `approved_on` |
| `CH Task Checklist Item` | Task (`ch_checklist`) | `checklist_item`, `is_mandatory`, `is_done`, `done_by`, `done_on`, `remarks` |
| `CH Store Opening Readiness Result` | *transient* | `check_code`, `area`, `category`, `status`, `count`, `details`, `resolve_route`, `is_hard_blocker`, `severity` — the shape returned by `ch_erp15.closure.readiness.run_readiness()`; reuses `CH Closure Readiness Item` if that doctype is generalised rather than cloned |

## 18.7 Source of truth — every major field

| Data | Single source of truth | How the project sees it | Stored on the project? |
|---|---|---|---|
| Store code, store id | `CH Store.store_code` / `store_id` (generated by `ch_store._generate_prefixed_store_code`) | Link | **No** |
| Warehouse, cost centre, POS Profile | `CH Store.warehouse` / `Cost Center` / `POS Profile` | read-only Link, set at stage 9 | Link only |
| Accounts | `Account` / `Company` defaults (`accounts_setup.py`) | read-only | **No** |
| Approved budget | `Budget` (`budget_against = Project`) | queried | mirrored to `budget_amount` **read-only, recomputed on readiness run** |
| Committed cost | submitted `Purchase Order Item.project` | aggregated | cached read-only, recomputed |
| Invoiced cost | `Project.total_purchase_cost` (ERPNext maintains it) | read | cached read-only |
| Amount paid | `Payment Entry Reference` → PI | aggregated | cached read-only |
| Budget variance | derived from the above | computed | derived, never authored |
| Task schedule, progress | `Task` | queried | **No** |
| Overall % complete | `Project.percent_complete` (method **Task Weight**) | read | **No** |
| Actual effort | `Timesheet` → `Task.actual_time` | read | **No** |
| Readiness %, blocker count | readiness run over registered checks | computed | stored **with `readiness_run_on`**, so staleness is visible |
| Documents | `CH Store Opening Document` → `File` | queried | **No** |
| Assets | `Asset` (`ch_project`, `ch_store`) | queried | **No** |
| Stock | `Bin` / `Stock Ledger Entry` | queried | **No** |
| Headcount, onboarding, training | `CH Workforce Plan Line`, `CH Onboarding Journey`, `CH Course Enrollment` (ch_hrms) | queried | **No** |
| Lease, deposit | `CH Lease` (ch_assets) | Link | **No** |
| Capex | `CH Capex Request` (ch_assets) | Link | **No** |
| Vendor | `Supplier` | Link on Task (permlevel 1) | **No** |
| Opening date | `CH Store Opening.approved_opening_datetime` → copied to `CH Store.opening_date` **at Opened** | — | authored here, then handed over |

**The five cached financial fields are the only deliberate denormalisation.** They exist because
a portfolio list of 30 store openings cannot aggregate five transaction tables per row at list-view
speed. They are **read-only in every DocPerm**, recomputed on the readiness run and on a scheduled
job, and always displayed alongside `readiness_run_on` so a stale figure is visible as stale.
Every §16.7 *control* (budget overrun stop, unapproved expenditure, unreceived material) reads
the live transaction, never the cache.

## 18.8 Duplicate-data risks

| # | Risk | Control |
|---|---|---|
| D1 | Store attributes drift between `CH Store Opening` and `CH Store` after go-live | after `Opened`, the proposal's location and identity fields become **read-only**; `CH Store` is authoritative. The proposal keeps its values as the historical *proposal*, which is the point |
| D2 | Cached financials go stale and are read as live | read-only + `readiness_run_on` shown beside them + all gates read live transactions |
| D3 | A second store master emerges | `CH Store Opening` is explicitly **not** a store; it holds no `warehouse`/`pos_profile` of its own beyond read-only links |
| D4 | Task financial fields diverge from PO/PI | only `ch_budgeted_cost` is authored; actual/committed are derived |
| D5 | A second headcount number | headcount lives only in `CH Workforce Plan Line`; the readiness check reads it |
| D6 | Document bytes stored twice | the register holds metadata and points at `File` |
| D7 | Two "task owner" fields | assignment (`ToDo`) is the only owner; no `Employee` link added to `Task` |
| D8 | Branch and CH Store both claiming store identity | resolved by the §17.6 decision **before** Phase 3 |
| D9 | A second readiness engine | checks register into `ch_erp15.closure.readiness`; no parallel registry |
| D10 | A second provisioning path creating duplicate masters | all provisioning calls the existing `ch_store` / `accounts_setup` functions, logged with a unique index |

## 18.9 Migration requirements

**No data migration.** `Project`, `Task`, `Project Template`, `Task Type`, `Timesheet`, `Issue`,
`Budget` and `Asset` are all at **0 rows**; there are **0** CH custom fields on `Project`/`Task`
today. Nothing converts.

Install/migrate work, in order:

| # | Step | Idempotent? | Notes |
|---|---|---|---|
| M1 | `before_install` asserts `ch_erp15`, `ch_item_master`, `ch_hrms`, `ch_assets` are installed | n/a | `required_apps = ["frappe", "erpnext"]` only — the `ch_assets`/`ch_hrms` pattern, to stay out of the `required_apps` cycle |
| M2 | Install custom fields from fixtures | yes | `create_custom_fields(..., update=True)` |
| M3 | Seed `Project Type` = "New Store Opening" | yes | `db.exists` guard |
| M4 | Seed `Task Type` — one per workstream (9) | yes | |
| M5 | Seed `CH Store Format` from the business list | yes | |
| M6 | Seed `CH Store Opening Document Type` (~40) | yes | |
| M7 | Seed `CH Store Opening Readiness Section` (14) | yes | |
| M8 | Seed `CH Store Opening Delay Cause` | yes | |
| M9 | Create the template `Task` tree + `Project Template` (report `19`) | yes | keyed on subject + `is_template` |
| M10 | Seed `CH Store Opening Requirement Rule` for the states actually operated in | yes | 38 `CH State` rows exist; seed only where stores exist |
| M11 | Install roles + DocPerms + permlevel-1 perms | yes | |
| M12 | Install the `Workflow` — **only after §17.1 is confirmed** | yes | |
| M13 | Register readiness checks | n/a | import-time registration; `register_check` already replaces on same `check_code` |
| M14 | Install the Workspace | **special** | Workspace JSON is **skipped unless its `modified` is newer than the DB row** — ship forward-dated or `import_file_by_path(path, force=True)` |
| M15 | **Branch back-fill** for 56 stores | yes | **only if decision B1** (report `17 §17.6`) |
| M16 | Back-fill `CH Store.opening_date` for 53 Active stores | yes | currently NULL on all 56; needed before the §16.11 "average lead time" report means anything. Source is outside ERPNext — a business data-collection task, not a script |

Each step commits **independently**. No single `@atomic` block: this bench has already been left
with a half-migrated site by an `@atomic` `post_schema_updates` that hit a missing dependency.

## 18.10 Indexing requirements

| Table | Index | Why |
|---|---|---|
| `tabCH Store Opening` | `company`, `city`, `stage`, `approved_opening_datetime` | portfolio list, calendar, scope filter |
| `tabCH Store Opening` | `project` (unique, nullable), `ch_store` (unique, nullable) | enforces the 1:1s at the database level, not just in code |
| `tabCH Store Opening Provision Log` | **UNIQUE (`store_opening`, `action`)** | the structural guarantee behind §16.12 idempotency, retry-safety and duplicate prevention |
| `tabCH Store Opening Document` | (`store_opening`, `document_type`, `is_current`) | current-version lookup |
| `tabCH Store Opening Document` | `expiry_date` | the expiry cron scans by date |
| `tabTask` | `ch_store_opening` | every readiness check and dashboard card filters on it |
| `tabTask` | (`ch_store_opening`, `ch_is_mandatory`, `status`) | the go-live blocker query — the hottest query in the system |
| `tabTask` | (`ch_store_opening`, `ch_is_critical_path`, `status`) | critical-path delay |
| `tabTask` | `ch_blocked` | blocker dashboard |
| `tabIssue` | `ch_store_opening` | stabilisation |
| `tabAsset` | `ch_project`, `ch_store` | asset reconciliation at closure |
| `tabCH Store Opening Requirement Rule` | (`applies_to_state`, `applies_to_brand`, `is_active`) | rule matching at project creation |

`Project.name` on `Material Request Item` / `Purchase Order Item` / `Purchase Receipt Item` /
`Purchase Invoice Item` — verify the `project` column is indexed on each before relying on the
budget-variance report at scale; ERPNext indexes some but not all of these.

> **Query gotcha carried forward from this bench:** `frappe.get_list` **rejects SQL functions in
> `order_by`**. The portfolio and calendar reports must sort on stored columns, not on
> `DATEDIFF(...)` or `COALESCE(...)`. Compute the sort key into a column or sort in Python.

## 18.11 Permission model

Full matrix in report `17 §17.4`. Structural summary:

| Layer | Mechanism |
|---|---|
| Role → DocPerm | 8 new `CH ` roles + 6 existing; read/write/create/submit/cancel/amend per role |
| Field-level confidentiality | **permlevel 1** on 5 financial fields of `CH Store Opening` and 2 of `Task` |
| Record-level scope | `permission_query_conditions` + `has_permission`, delegating to `ch_erp15.ch_erp15.scope` (Company → City → Zone → Store) |
| Row-level ownership | Frappe assignment (`ToDo`) governs which tasks a department owner may complete |
| Stage authority | `Workflow Transition.allowed` |
| Approval authority | `CH Approval Authority` — `doctype_target`, `action`, `role`, `company`, amount band, `priority` |
| Document access | DocPerm on `CH Store Opening Document` + `File.is_private` |
| Audit access | `CH Store Opening Auditor` — read everywhere including permlevel 1, write nowhere |

**Two constraints the schema itself enforces:**

1. `project_manager` and `business_sponsor` are `Link → User`, **never** `Link → Employee` —
   Frappe ANDs one user-permission clause per Link field, and two Employee links on one doctype
   produce an unsatisfiable filter (this hit 8 `ch_hrms` doctypes).
2. Every readiness check and permission guard is exercised **as a non-bypass user** in the test
   suite. An import-only assertion has passed on this bench before while the guard was unbound.
