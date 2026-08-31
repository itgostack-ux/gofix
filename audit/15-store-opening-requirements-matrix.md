# 15 — Store Opening Requirements Matrix

Every requirement in §16.1–16.13 mapped to a disposition under the mandated decision order:

| Code | Meaning |
|---|---|
| **S** | Standard ERPNext/Frappe DocType or feature, used as-is |
| **X** | Extend a standard DocType (Custom Field / Property Setter / Workflow / Client or Server Script) |
| **R** | Reuse an existing **CH** app object already on this bench |
| **N** | New DocType required — the information cannot be represented correctly by an existing structure |
| **A** | Automation / server logic (no new schema) |

Source of truth is stated for every row so no figure is duplicated.

---

## 15.1 Store Opening Project (§16.1)

Container decision: **`CH Store Opening` — one new DocType (N)**, `Link → Project` (1:1).

Rationale: `Project` cannot carry the pre-approval life of a store. A store opening is
*proposed*, *evaluated* and *approved or rejected* before any schedule exists — and rejected
proposals must be retained for the §16.11 portfolio and §16.13 lessons-learned. Forcing that
onto `Project` means creating Projects for stores that never open, which corrupts
`project_summary`, every Budget-against-Project figure, and the Projects workspace. The
proposal is also **submittable** (`docstatus` gives the approve/reject audit primitive);
`Project` is not submittable and cannot be made so safely.

`Project` remains the **schedule of record**. `CH Store Opening` is the **control record**.

| §16.1 field | Disposition | Where it lives | Source of truth |
|---|---|---|---|
| Unique project/store-opening ID | **N** | `CH Store Opening.name` — `format:SO-{YY}{MM}-{####}` | itself |
| Proposed store name | **N** | `proposed_store_name` (Data) | itself until go-live, then `CH Store.store_name` |
| Brand | **S+N** | `brand` → `Brand` (174 rows) | `Brand` |
| Company / legal entity | **S+N** | `company` → `Company` (4 rows) | `Company` |
| Store format | **N** | `store_format` → new master `CH Store Format` | `CH Store Format` |
| Region | **R+N** | `zone` → `CH Store Zone` (9 rows) | `CH Store Zone` |
| State | **R+N** | `state` → `CH State` (38 rows) | `CH State` |
| City | **R+N** | `city` → `CH City` (815 rows) | `CH City` |
| Full address | **S+N** | `address` → `Address` (standard link) | `Address` |
| Location coordinates | **R+N** | `latitude` / `longitude` (Float) | itself → synced to `CH Store` by the existing `sync_store_geo_to_warehouses` path |
| Proposed branch | **X+N** | `branch` → `Branch` — see report `14 §14.7` before enabling | `Branch` |
| Proposed warehouse | **A** | **not stored** — resolved from `CH Store.warehouse` after provisioning | `CH Store` |
| Proposed cost centre | **A** | **not stored** — resolved from `ch_store.ensure_store_cost_center` | `Cost Center` |
| Project manager | **N** | `project_manager` → `User` | `User` |
| Projects Team members | **N** | child table `CH Store Opening Member` (`user`, `role_in_project`, `department`) | itself; mirrored into `Project User` |
| Business sponsor | **N** | `business_sponsor` → `User` | `User` |
| Department owners | **N** | `CH Store Opening Member.department` → `Department` (53 rows) | `Department` |
| Proposed opening date/time | **N** | `proposed_opening_datetime` (Datetime) | itself |
| Approved opening date/time | **N** | `approved_opening_datetime` (Datetime, read-only, set by approval) | itself |
| Project start date | **S** | `Project.expected_start_date` | `Project` |
| Current project stage | **N** | `stage` (Select) + `Workflow` — see report `17` | itself |
| Overall percentage completion | **S** | `Project.percent_complete`, method **Task Weight** | `Project` |
| Budget | **S** | `Budget` with `budget_against = Project` | `Budget` |
| Actual committed cost | **A** | computed from submitted `Purchase Order Item.project` | `Purchase Order` |
| Actual invoiced cost | **S** | `Project.total_purchase_cost` (ERPNext maintains it) | `Purchase Invoice` |
| Amount paid | **A** | computed from `Payment Entry Reference` against those PIs | `Payment Entry` |
| Budget variance | **A** | derived — never stored | derived |
| Project health | **A** | derived (schedule × budget × blockers) | derived |
| Overall risk status | **A** | derived from open blockers + critical-path slack | derived |
| Opening-readiness score | **R** | readiness engine, stored on the readiness run (§15.6) | readiness run |
| Blockers | **A** | tasks where `blocked = 1`, and Fail-status readiness rows | `Task` / readiness |
| Dependencies | **S** | `Task Depends On` | `Task` |
| Required approvals | **R+N** | `CH Approval Authority` (26 rows) + `CH Store Opening Signoff` | `CH Approval Authority` |
| Associated documents | **N** | `CH Store Opening Document` (§15.8) | itself |
| Store-opening photographs | **N** | `CH Store Opening Document` with `document_type` category *Photograph* | itself |
| Final handover records | **N** | `CH Store Opening Signoff` rows of type *Handover* | itself |
| Post-opening review | **N** | `CH Store Opening Closure` (§15.13) | itself |

**Explicitly NOT duplicated onto the project:** purchase amounts, invoice numbers, payment
records, stock quantities, asset details, employee records. All are read live from ERPNext by
`project` / `CH Store` link.

## 15.2 Standard Store Opening Template (§16.2)

| Requirement | Disposition | Detail |
|---|---|---|
| Reusable template | **S** | `Project Template` + template `Task` rows (`is_template=1`) |
| Auto-generate tasks on project creation | **S** | `Project.copy_from_template()` fires on `after_insert` |
| Grouped into workstreams | **S** | one `is_group=1` template Task per workstream, children beneath (NestedSet) |
| Dependencies preserved from template | **S** | `dependency_mapping()` re-points `depends_on` onto the created tasks |
| 9 workstreams / ~200 tasks | **S** | full library in report `19` |
| **Configurable by state, city, brand, store type** | **N** | `CH Store Opening Requirement Rule` — see below |
| Statutory requirements not hardcoded | **N** | same |

**`CH Store Opening Requirement Rule` (N)** is unavoidable. `Project Template` has exactly three
fields (`project_type`, `tasks`, `disabled`); it cannot express "Fire NOC applies in Tamil Nadu
above 500 sq ft but not in Kerala". Modelling this as one template per state×brand×format would
mean 38 × 174 × n templates and guarantees drift. The rule doctype carries
`applies_to_state / city / brand / store_format / company`, a `Link → Task` (the template task
to inject), `is_mandatory`, `blocks_go_live`, and `requirement_category`
(Statutory / Licence / Safety / Fitout / IT / Finance). Applied **after** `copy_from_template`
by a single server hook. This is the §16.2 "must be configurable by state, city, brand and store
type" clause, and it is the only correct place for it.

## 15.3 Task-management requirements (§16.3)

`Task` is extended, not replaced. Of the 28 attributes required, **13 already exist**.

| Attribute | Disposition | Field |
|---|---|---|
| Workstream/category | **S** | `parent_task` (group task) + `Task.type` → `Task Type` |
| Task owner | **S** | Frappe assignment (`ToDo` / `_assign`) |
| Supporting team members | **S** | additional `ToDo` assignments |
| Responsible department | **S** | `Task.department` |
| Approver | **X** | `ch_approver` (Link → User) |
| Planned start / finish | **S** | `exp_start_date` / `exp_end_date` (Datetime) |
| Actual start / finish | **S** | `act_start_date` / `act_end_date` |
| Estimated / actual effort | **S** | `expected_time` / `actual_time` (+ `Timesheet`) |
| Priority | **S** | `priority` |
| Status | **S** | `status` |
| Percentage completion | **S** | `progress` |
| Dependencies, predecessor/successor | **S** | `Task Depends On` + `depends_on_tasks` |
| Milestone indicator | **S** | `is_milestone` |
| **Mandatory/optional** | **X** | `ch_is_mandatory` (Check) |
| **Critical-path indicator** | **X** | `ch_is_critical_path` (Check, computed — read-only) |
| **SLA** | **X** | `ch_sla_hours` (Int) + `ch_sla_target` (Datetime, computed) |
| **Real-time time remaining** | **A** | derived at read time from `ch_sla_target` — **never stored** (a stored countdown is stale the moment it is written) |
| **Overdue status** | **A** | derived; ERPNext's `set_tasks_as_overdue` cron already flips `status = Overdue` |
| **Blocked status / reason** | **X** | `ch_blocked` (Check), `ch_blocked_reason` (Small Text), `ch_blocked_since` (Datetime) |
| **Risk level** | **X** | `ch_risk_level` (Select: Low/Medium/High/Critical) |
| **Budgeted cost** | **X** | `ch_budgeted_cost` (Currency) |
| Actual/committed cost | **A** | derived from `Purchase Order Item` / `Purchase Invoice Item` where `project` matches and the task is the linked requirement — **not stored** |
| **Vendor** | **X** | `ch_supplier` (Link → Supplier, 863 rows) |
| Related procurement record | **X** | `ch_material_request` (Link → Material Request); PO/PR/PI reached through it, not copied |
| Related asset | **X** | `ch_asset` (Link → Asset) |
| **Required checklist** | **N** | child table `CH Task Checklist Item` (`item`, `is_mandatory`, `is_done`, `done_by`, `done_on`, `remarks`) |
| **Evidence/attachment** | **X** | `ch_requires_evidence` (Check); evidence is the standard `File` attachment — counted, not re-stored |
| Comments | **S** | Frappe `Comment` |
| **Approval / rejection reason** | **X** | `ch_approval_status` (Select: Not Required/Pending/Approved/Rejected), `ch_approved_by`, `ch_approved_on`, `ch_rejection_reason` |
| Completion evidence | **A** | validated on `validate` — see below |
| Audit history | **S** | Frappe `Version` (already 465,884 rows live) |

**Server-side completion gate (A) — the §16.3 hard requirement.** A `Task.validate` hook on
`ch_erp15`/the new app must throw when `status` moves to `Completed` and any of:
mandatory checklist rows not `is_done`; `ch_requires_evidence` with zero attachments in `File`;
`ch_approval_status` in (`Pending`, `Rejected`). Deliberately **on `validate`, not `before_save`
and not client-side** — the requirement says server-validated, and `validate` is the only point
that also catches `db_set`-free API writes, bulk `set_multiple_status`, and the Kanban drag path.
ERPNext's own predecessor check lives in `validate_status`, so the new gate sits beside a proven
one.

**Field-name prefix:** `ch_` throughout, matching `Asset.ch_asset_tag` / `Branch.ch_city`.
The bench uses both `ch_` (ch_erp15/ch_assets) and `custom_` (ch_pos/logistics); `ch_` is correct
for new master-side fields. This matters — memory records two custom fields that existed only in
the local DB and never in code; every field here ships as a fixture (§15.14).

## 15.4 Dependencies and schedule control (§16.4)

| Requirement | Disposition | Detail |
|---|---|---|
| Finish-to-start dependencies | **S** | `Task Depends On`; enforced by `Task.validate_status()` |
| Parallel tasks | **S** | absence of a dependency row |
| Milestones | **S** | `Task.is_milestone` |
| **Critical-path visibility** | **A** | new: forward/backward pass over `Task Depends On` → sets `ch_is_critical_path`, `ch_total_float` (Float, days). **Gap G1** |
| **Automatic impact calculation on delay** | **A** | recompute successors' earliest start; report the shift, do not apply it silently |
| **Warning when a delay threatens the opening date** | **A** | compare critical-path finish vs `approved_opening_datetime` → `ch_opening_date_at_risk` on the store opening. **Gap G3** |
| Automatic recalculation of project health | **A** | scheduled + on-change |
| Escalation for overdue critical tasks | **R** | `ch_erp15.sla_engine` tiering + `notification_router` |
| **Rescheduling with reasons** | **N** | `CH Store Opening Date Revision` (see below) |
| **Complete schedule-change history** | **N** | same |
| **Opening-date revision history** | **N** | same |
| Notifications to affected task owners | **R** | `notification_router` |

**`Task.reschedule_dependent_tasks()` must be neutralised (Gap G2).** It silently rewrites
successor dates with no reason and no history — directly contrary to §16.4. Two options, decided
in report `17 §17.5`: (a) leave it for non-store-opening projects and skip it for these by
setting `flags.ignore_recursion_check` semantics via a `Task` override, or (b) an
`override_doctype_class` on `Task`. **(a) is preferred** — report `05`'s integration finding is
that this bench currently has **zero** `override_doctype_class` collisions across 8 custom apps,
and overriding `Task` would be the first. Any material date revision writes a
`CH Store Opening Date Revision` row: `previous_datetime`, `revised_datetime`, `reason`,
`requested_by`, `approved_by`, `approved_on`, `is_committed_date`.

## 15.5 Project stages and controlled workflow (§16.5)

| Requirement | Disposition |
|---|---|
| 18-stage lifecycle | **S** | `Workflow` on `CH Store Opening` (13 workflows already live on this site) |
| Authorized role per transition | **S** | `Workflow Transition.allowed` |
| Mandatory completed tasks per transition | **A** | `condition` cannot query tasks safely → server guard, mirroring `ch_erp15/closure/guards.py` |
| Mandatory documents | **A** | same guard |
| Required approval | **R** | `CH Approval Authority` (`doctype_target`, `action`, `role`, `company`, amount bands, `priority`, `condition`) |
| Budget / stock / IT / finance / operational validation | **R** | readiness checks by `category` (§15.6) |
| Notification | **R** | `notification_router` |
| Audit entry | **N** | `CH Store Opening Audit Log` child table — mirrors `CH Closure Audit Log` |
| Reversal rules | **A** | explicit allowed-reversal map in the transition guard |
| **Must not reach Ready to Open / Opened with critical tasks open** | **A** | hard blocker in the guard — see §15.6 |

Stage list is **provisional pending business confirmation** (§16.5 says so explicitly). Report
`17 §17.1` carries the proposed 18 stages, and flags which four are candidates for merging.

## 15.6 Go-live readiness checklist (§16.6)

**Disposition: R — extend `ch_erp15/closure/readiness.py`, do not write a second engine.**

The engine is entity-type keyed (`_REGISTRY: Dict[str, List[CheckSpec]]`) and already registers
`Warehouse`, `CH Store`, `Department`, `Company` — including **14 `CH Store` checks**. Adding
`"CH Store Opening"` as a fifth entity type is a one-line registry addition plus a new
`checks/store_opening.py`. `CheckSpec.hard` is exactly the §16.6 "blocking behaviour regardless
of the overall percentage" flag; `ReadinessResult.severity` and `resolve_route` are already there.

| §16.6 checklist section | `CheckSpec.category` | Accountable owner / approver (§16.9 role) |
|---|---|---|
| Property and legal readiness | `Legal` | Legal Team / Department Head |
| Statutory compliance | `Compliance` | Compliance Owner / Management Approver |
| Civil and interior completion | `Fitout` | Projects Team Member / Project Manager |
| Fire and physical safety | `Safety` | Safety Owner / Management Approver |
| IT readiness | `IT` | IT Team / IT Head |
| ERPNext readiness | `Master` | System Administrator / IT Head |
| POS readiness | `POS` | POS Manager / Operations Team |
| Payment readiness | `Finance` | Finance Team / Finance Head |
| Inventory readiness | `Inventory` | Operations Team / Store Manager |
| Staff readiness | `HR` | HR Team / Department Head |
| Finance readiness | `Finance` | Finance Team / Finance Head |
| Marketing readiness | `Marketing` | Marketing Team / Department Head |
| Security readiness | `Security` | IT Team / Operations Team |
| Management approval | `Approval` | Management Approver |

Owner/approver per section is data, not code: **`CH Store Opening Readiness Section` (N)** —
`section`, `owner_role`, `approver_role`, `weight`, `is_blocking`. This keeps §16.9's
"each section must have an accountable owner and approver" configurable per company.

**Scoring rule (§16.6's anti-average clause).** Two numbers are published, never one:
`readiness_percent` (weighted, informational) and `blocker_count` (hard). The go/no-go
recommendation is **`blocker_count == 0`**, not a percentage threshold. This mirrors
`CH Closure Request`, which already stores `readiness_score`, `pass_count`, `warning_count` and
`blocker_count` as four separate fields for exactly this reason.

Dashboard items (readiness % by workstream, overall %, critical blockers, overdue critical
tasks, pending approvals, missing documents, budget variance, tasks threatening the opening
date, department-wise status, go/no-go) are all **derived** — Number Cards and a Script Report
over `Task` / readiness rows / `Purchase Order` / `Budget`. **No new storage.**

## 15.7 Budget, procurement and cost control (§16.7)

| Requirement | Disposition | Mechanism |
|---|---|---|
| Approved project budget | **S** | `Budget.budget_against = Project` |
| Material Requests | **S** | `Material Request Item.project` (+ existing `Material Request.custom_store`) |
| RFQs | **X** | `Request for Quotation` has **no** `project` field (Gap G10) → add `ch_project` |
| Supplier Quotations | **S** | `Supplier Quotation Item.project` |
| Purchase Orders | **S** | `Purchase Order Item.project` (+ existing `Purchase Order.custom_target_store`) |
| Change orders | **S** | PO amendment (`amended_from`) + `Version` |
| Purchase Receipts | **S** | `Purchase Receipt Item.project` |
| Purchase Invoices | **S** | `Purchase Invoice Item.project` |
| Payment status | **S** | `Payment Entry Reference` → PI |
| Assets | **X** | `Asset` has **no** `project` field (Gap G9) → add `ch_project` **and** `ch_store` |
| Expenses | **S** | `Expense Claim.project` (HRMS already adds `Project.total_expense_claim`) |
| Committed cost | **A** | Σ submitted PO amount − Σ received, by project |
| Actual cost | **S** | `Project.total_purchase_cost` |
| Remaining budget / variance | **A** | derived |
| Unapproved expenditure | **A** | PO/PI on the project with no approved `CH Capex Request` or requirement task |
| **PO exceeding approved limits** | **S** | `Budget` `applicable_on_purchase_order` + `action_if_annual_budget_exceeded = Stop`. **No custom code.** |
| **Duplicate procurement for the same requirement** | **A** | guard: >1 open MR/PO against the same `Task` |
| **Closure with pending supplier invoices / unreceived material** | **R** | readiness check, `hard=True`, category `Finance`/`Inventory` |
| **Asset creation without project/store mapping** | **A** | `Asset.validate` guard once `ch_project`/`ch_store` exist |
| Unexplained variance | **A** | `CH Store Opening Budget Variance` report |

Capex uses the existing **`CH Capex Request`** (already links `material_request`,
`purchase_order`, `created_asset`, `budget_account`, `budget_available`,
`required_approver_role`) — extended with `ch_store_opening`. Lease and deposit use the existing
**`CH Lease`**, which already has `ch_store`, `security_deposit` and `commencement_date`; add
`ch_store_opening`. **Neither is re-modelled.**

## 15.8 Documents and approvals (§16.8)

**Disposition: N — `CH Store Opening Document` + `CH Store Opening Document Type`**, modelled
directly on the proven `CH Employee Document` / `CH Employee Document Type` pair in ch_hrms.

Frappe `File` cannot carry `valid_from`, `expiry_date`, `version`, `approval_status` or
`approved_by`; nothing in ERPNext does. The register **points at** `File`, it does not store the
bytes twice.

| §16.8 attribute | Field |
|---|---|
| Document type | `document_type` → `CH Store Opening Document Type` |
| Version | `version` (Int, auto-incremented per (`store_opening`, `document_type`)) |
| Uploaded by / date | `owner` / `creation` (standard) |
| Valid-from / Expiry | `valid_from` / `expiry_date` (Date) + `days_to_expiry` (Int, computed) |
| Approval status | `approval_status` (Select: Pending/Verified/Rejected/Expired) |
| Approved by | `verified_by` (Link → User) + `verified_on` |
| Related task | `task` (Link → Task) |
| Renewal requirement | `requires_renewal` (Check) + `renewal_lead_days` (Int) |
| File | `document_file` (Attach) + `is_private` |

**"Do not overwrite historical versions without preserving the audit trail":** a superseding
upload inserts a **new row** with `version + 1` and sets the prior row `is_current = 0`. Never
an in-place edit. `CH Employee Document` uses `format:EMPDOC-{employee}-{####}`; use
`format:SOD-{store_opening}-{####}` for symmetry.

`CH Store Opening Document Type` carries the §16.2 configurability: `applies_to_state`,
`applies_to_brand`, `applies_to_store_format`, `is_statutory`, `blocks_go_live`,
`default_validity_months`.

## 15.9 Roles and permissions (§16.9)

| Requirement | Disposition |
|---|---|
| 14 roles | **S+R** | `Projects Manager` and `Projects User` already exist; new roles follow the `CH ` convention (report `17 §17.3`) |
| Company access | **R** | `CH User Scope Company` |
| Project access | **S** | `Project User` + DocPerm `if_owner` / `permission_query_conditions` |
| Department access | **S** | `Task.department` in the query condition |
| Branch/store access | **R** | `CH User Scope Store` / `Zone` / `City` — the hierarchical scope already registered for 20 doctypes |
| Budget visibility | **S** | **`permlevel = 1`** on the financial fields, granted only to Finance/PM roles |
| Vendor & commercial confidentiality | **S** | `permlevel = 1` on `ch_supplier`, `ch_budgeted_cost` |
| Approval authority | **R** | `CH Approval Authority` (26 rows live) |
| Task-update rights | **S** | DocPerm + assignment |
| Stage-transition rights | **S** | `Workflow Transition.allowed` |
| Document access | **S** | DocPerm on `CH Store Opening Document` + `is_private` on the File |
| Project closure rights | **S** | workflow transition restricted to Management Approver |

**§16.9's key sentence** — "a task owner should update their assigned tasks without receiving
unnecessary access to confidential project financials" — is satisfied by **permlevel 1**, not by
a custom permission layer. That is standard Frappe and it is the smallest correct answer.
Full matrix: report `17`.

## 15.10 Notifications and escalation (§16.10)

**Disposition: R** — `ch_erp15.notification_router` + `CH Notification Settings` (role×scope
fan-out, company-scoped, fail-closed) and `ch_erp15.sla_engine` (tier at 🟡 warning / 🔴 breached
/ 🚨 critical, on the live `*/15 * * * *` cron). 14 notification events map onto the existing
router; **no new notification framework**.

**Anti-flooding (§16.10's explicit constraint):** routine reminders (upcoming deadline, pending
approval, missing evidence) are **batched into one digest per recipient per day** by a new
scheduled job in the pattern of `ch_erp15/closure/scheduler.py`. Only four conditions notify
immediately: critical-path delay, opening-date-at-risk, budget threshold exceeded, go-live
approval required. This is a design constraint on the sender, not a user preference.

## 15.11 Workspace and dashboards (§16.11)

**Disposition: S** — one new `Workspace` ("New Store Projects"), 14 shortcuts/cards, 17 reports.
Report definitions in report `19 §19.6`.

> **Gotcha (from this bench's own history):** a Workspace JSON is **skipped on migrate unless
> its `modified` timestamp is newer than the DB row**. Ship the workspace with a forward
> `modified`, or import it explicitly with `import_file_by_path(path, force=True)`. Also:
> Workspace shortcut `stats_filter` fieldnames are **never validated** — a wrong fieldname
> produces a runtime "Field not permitted in query" on the workspace, not a build error.
> Workspace icons are *timeless*; sidebar icons are *lucide*.

## 15.12 Automations and integrations (§16.12)

| Automation | Disposition | Reuses |
|---|---|---|
| Create Project from approved proposal | **A** | `frappe.new_doc("Project")`, guarded by `if self.project: return` |
| Apply correct Project Template | **S** | `Project.project_template` → `copy_from_template()` |
| Assign tasks by department and region | **S** | `Assignment Rule` (doctype present, **0 configured**) |
| **Create the Branch** at the approved stage | **A** | see report `14 §14.7` — decision required first |
| **Create Warehouse structures** | **R** | `ch_store.ensure_store_bins()` |
| **Create Cost Center** | **R** | `ch_store.ensure_store_cost_center_hierarchy()` |
| **Create POS Profile** | **R** | `ch_store.ensure_store_pos_profile()` |
| **Create the store code** | **R** | `ch_store._generate_prefixed_store_code()` (`Company.store_code_prefix`) + `next_free_numeric_id("store")` |
| **Create the accounts** | **R** | `accounts_setup.setup_company_accounts()` / `_ensure_store_cost_centers()` |
| Initiate user-access requests | **R** | `CH User Scope` + `ch_erp15.onboarding` |
| Initiate opening-stock transfer | **S+R** | `Material Request` (`custom_store`) → `Stock Entry` → `CH Transfer Manifest` |
| Create assets from received capital items | **S** | ERPNext auto-creates `Asset` from `Purchase Receipt` when the Item is a fixed asset; stamp `ch_project`/`ch_store` in `Asset.validate` |
| Add store to website / store locator | **A** | existing store-locator path (`sync_store_geo_to_warehouses` / geocoding) |
| Trigger pre-opening tests | **A** | readiness run |
| Trigger department sign-offs | **N** | `CH Store Opening Signoff` |
| Handover to Operations | **A** | stage transition + signoff |
| Post-opening support period | **N** | `CH Store Opening Closure` |

**The single most important automation decision:** all master creation routes through the
**existing `ch_store` functions**, called at an approved stage. Every one is already
existence-checked and idempotent, and has been exercised across 56 stores. Writing a second
provisioning path would be the exact "duplicate master creation" §16.12 forbids.

The seven §16.12 quality bars map as follows:

| Bar | How |
|---|---|
| Idempotent | existing `frappe.db.exists` guards + a `CH Store Opening Provision Log` row keyed on (`store_opening`, `action`) with a **unique index** |
| Permission-controlled | `@frappe.whitelist()` + explicit role check + `CH Approval Authority` lookup. **Never `ignore_permissions=True` on the entry point** — only on the inner master insert, as `ch_store` already does |
| Transaction-safe | one master per call, each committing independently; **not** one `@atomic` block. This bench has already been bitten by an `@atomic` `post_schema_updates` leaving a half-migrated site |
| Fully logged | `CH Store Opening Provision Log` (`action`, `status`, `created_document`, `error`, `run_by`, `run_on`) |
| Retry-safe | the log's unique index makes a retry a no-op that returns the existing document |
| Reversible where feasible | Branch/Cost Center/POS Profile can be disabled, not deleted; Warehouse deletion is blocked once an SLE exists — record this honestly rather than promising rollback |
| Duplicate-protected | the unique index, plus the pre-existing `store_code` uniqueness (`autoname: field:store_code`) |

## 15.13 Stabilisation and closure (§16.13)

| Requirement | Disposition |
|---|---|
| Configurable stabilisation period | **N** | `stabilisation_days` on `CH Store Opening` (default from settings) |
| 11 issue categories during stabilisation | **S** | `Issue` — **currently 0 rows**; `Issue.project` links it. Use `Issue Type` for the categories |
| Final issue review | **A** | readiness check: no open `Issue` on the project |
| Budget reconciliation | **A** | variance report, `hard` blocker if unexplained |
| Pending-procurement review | **A** | readiness check: no open PO/MR |
| Asset reconciliation | **A** | readiness check via `ch_assets` `CH Asset Verification` |
| Document-completeness review | **A** | readiness check over `CH Store Opening Document` |
| Department handover | **N** | `CH Store Opening Signoff` |
| Lessons learned | **N** | `CH Store Opening Closure` (`what_went_well`, `what_delayed_us`, `delay_cause` → `CH Store Opening Delay Cause`) |
| Final closure approval | **S** | workflow transition |
| **Preserve history for comparing openings** | **S** | the `CH Store Opening` record itself is never deleted; `delay_cause` is a **master link**, not free text, so §16.11's "repeated causes of delay" report is a `GROUP BY` and not a text-mining exercise |

## 15.14 New objects, totalled

**New DocTypes (12)**

| # | DocType | Type | Justification (why not standard) |
|---|---|---|---|
| 1 | `CH Store Opening` | submittable master | pre-approval life of a store; rejected proposals must not become Projects |
| 2 | `CH Store Opening Member` | child | Projects Team roster with `role_in_project` + `department`; `Project User` has neither |
| 3 | `CH Store Opening Signoff` | child | department sign-off with approver, date, verdict, remarks |
| 4 | `CH Store Opening Audit Log` | child | stage-transition audit; mirrors `CH Closure Audit Log` |
| 5 | `CH Store Opening Date Revision` | child | §16.4's mandatory previous/revised/user/reason/approval record |
| 6 | `CH Store Opening Document` | master | §16.8 register — version, validity, expiry, approval |
| 7 | `CH Store Opening Document Type` | master | statutory config by state/brand/format |
| 8 | `CH Store Opening Requirement Rule` | master | §16.2 "configurable by state, city, brand and store type" |
| 9 | `CH Store Opening Readiness Section` | master | §16.6 owner + approver + weight per section |
| 10 | `CH Store Opening Provision Log` | master | §16.12 idempotency + retry-safety + audit |
| 11 | `CH Store Opening Closure` | master | §16.13 post-opening review + lessons learned |
| 12 | `CH Store Format` | master | store format; no standard equivalent |

Plus two small option masters — `CH Store Opening Delay Cause`, `CH Task Checklist Item` (child).

**Custom fields on standard DocTypes (~22)**

| DocType | Fields |
|---|---|
| `Task` | `ch_store_opening`, `ch_is_mandatory`, `ch_is_critical_path`, `ch_total_float`, `ch_sla_hours`, `ch_sla_target`, `ch_blocked`, `ch_blocked_reason`, `ch_blocked_since`, `ch_risk_level`, `ch_budgeted_cost` (permlevel 1), `ch_supplier` (permlevel 1), `ch_material_request`, `ch_asset`, `ch_approver`, `ch_approval_status`, `ch_approved_by`, `ch_approved_on`, `ch_rejection_reason`, `ch_requires_evidence`, `ch_checklist` (Table) |
| `Project` | `ch_store_opening`, `ch_store` |
| `Asset` | `ch_project`, `ch_store` |
| `Request for Quotation` | `ch_project` |
| `CH Capex Request` | `ch_store_opening` |
| `CH Lease` | `ch_store_opening` |
| `CH Store` | `ch_store_opening` (back-link), `store_format` |

**Zero new DocTypes for:** budget, procurement, payment, stock, asset, employee, supplier,
customer, accounting. Every one of those is read live from ERPNext.

## 15.15 What is deliberately NOT built

| Tempting thing | Why not |
|---|---|
| A custom Task doctype | `Task` gives NestedSet, dependencies, FS enforcement, assignment, timesheets, Gantt, Kanban and 5 reports for free |
| A custom project/budget total | `Budget` already stops POs against a Project |
| A second readiness engine | `ch_erp15/closure/readiness.py` exists, is registry-driven and has 14 live `CH Store` checks |
| A second provisioning path | `ch_store.py` already provisions code, cost centre, POS profile and bins idempotently for 56 stores |
| A store-opening notification framework | `notification_router` + `sla_engine` are live on cron |
| Copying PO/PI/payment amounts onto the project | derived at read time; §16.7 forbids duplication |
| A custom file store | `File` + a register that points at it |
| A parallel HR/recruitment flow | `CH Workforce Plan Line` and `CH Onboarding Journey` **already link `ch_store`** |
