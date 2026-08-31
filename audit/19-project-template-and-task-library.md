# 19 — Project Template and Task Library

The reusable store-opening `Project Template` required by §16.2, plus the workspace and reports
of §16.11.

**Mechanism (all standard ERPNext):** template tasks are `Task` rows with `is_template = 1`.
One `is_group = 1` template task per workstream; detail tasks hang beneath it as NestedSet
children. `Project Template.tasks` lists them. On project creation
`Project.copy_from_template()` clones the tree and `dependency_mapping()` re-points every
`depends_on` and `parent_task` onto the newly created tasks.

**Then** `ch_projects` applies the `CH Store Opening Requirement Rule` set (report `18 §18.5`),
which injects or removes statutory tasks by state / city / brand / store format — so no
location's licence requirements are hardcoded into the template.

---

## 19.1 Template structure

```
Project Template: "New Store Opening — Standard"   (Project Type: New Store Opening)
├── W1  Business Approval & Planning      (group)    9 tasks
├── W2  Property & Legal                  (group)   14 tasks
├── W3  Design & Civil Work               (group)   19 tasks
├── W4  IT & Technology                   (group)   26 tasks
├── W5  Procurement & Assets              (group)   17 tasks
├── W6  People & Operations               (group)   15 tasks
├── W7  Inventory & Merchandising         (group)   12 tasks
├── W8  Finance & Compliance              (group)   14 tasks
├── W9  Marketing & Launch                (group)   13 tasks
└── W10 Go-Live & Handover                (group)   25 tasks
                                                   ─────
                                                   164 tasks
```

One template per **store format** (`CH Store Format.default_project_template`) — a kiosk and a
flagship do not share a task list. Variation *within* a format is handled by requirement rules,
not by more templates. This is the line that stops the 38 × 174 × n template explosion.

**Legend**

| Col | Meaning |
|---|---|
| **M** | `ch_is_mandatory` — go-live is blocked while incomplete |
| **★** | `is_milestone` |
| **E** | `ch_requires_evidence` — completion needs an attachment |
| **A** | approval required (`ch_approval_status` starts *Pending*) |
| **Dep** | predecessor task ID (finish-to-start) |
| **D** | planned duration in days |
| Owner | default `owner_role`, materialised as a Frappe assignment |

## 19.2 W1 — Business Approval & Planning (9)

| ID | Task | M | ★ | E | A | Owner | Dep | D |
|---|---|:-:|:-:|:-:|:-:|---|---|--:|
| W1.1 | Business case | ✓ | | ✓ | | CH Project Manager | — | 5 |
| W1.2 | Location proposal | ✓ | | ✓ | | CH Projects Team Member | — | 3 |
| W1.3 | Catchment analysis | ✓ | | ✓ | | CH Projects Team Member | W1.2 | 5 |
| W1.4 | Sales projection | ✓ | | ✓ | | CH Area Sales Manager | W1.3 | 3 |
| W1.5 | Capital-expenditure estimate | ✓ | | ✓ | | CH Project Manager | W1.3 | 3 |
| W1.6 | Operating-expenditure estimate | ✓ | | ✓ | | Accounts Manager | W1.4 | 3 |
| W1.7 | **Management approval of business case** | ✓ | ★ | | ✓ | CH Store Opening Approver | W1.1,W1.4,W1.5,W1.6 | 5 |
| W1.8 | Project kickoff | | ★ | | | CH Project Manager | W1.7 | 1 |
| W1.9 | Target opening-date approval | ✓ | ★ | | ✓ | CH Store Opening Approver | W1.7 | 2 |

W1.5 creates a `CH Capex Request` (ch_assets), linked by `ch_store_opening`. W1.7 is the
transition *Awaiting Approval → Approved*; the rest of the template is created **by** it.

## 19.3 W2 — Property & Legal (14)

| ID | Task | M | ★ | E | A | Owner | Dep | D |
|---|---|:-:|:-:|:-:|:-:|---|---|--:|
| W2.1 | Property identification | ✓ | | | | CH Projects Team Member | W1.8 | 10 |
| W2.2 | Site visit | ✓ | | ✓ | | CH Projects Team Member | W2.1 | 3 |
| W2.3 | Commercial negotiation | ✓ | | | | CH Project Manager | W2.2 | 10 |
| W2.4 | Legal due diligence | ✓ | | ✓ | ✓ | CH Department Head (Legal) | W2.3 | 10 |
| W2.5 | **Lease / rental agreement** | ✓ | ★ | ✓ | ✓ | CH Department Head (Legal) | W2.4 | 7 |
| W2.6 | Security deposit | ✓ | | ✓ | ✓ | Accounts Manager | W2.5 | 3 |
| W2.7 | Landlord approvals | ✓ | | ✓ | | CH Project Manager | W2.5 | 5 |
| W2.8 | Building approvals | ✓ | | ✓ | | CH Project Manager | W2.5 | 15 |
| W2.9 | Trade licence | ✓ | | ✓ | | CH Department Head (Legal) | W2.5 | 20 |
| W2.10 | Shops & Establishments registration | ✓ | | ✓ | | CH Department Head (Legal) | W2.5 | 15 |
| W2.11 | Fire & safety approvals | ✓ | | ✓ | ✓ | CH Department Head (Legal) | W3.14 | 20 |
| W2.12 | Local authority permissions | ✓ | | ✓ | | CH Department Head (Legal) | W2.5 | 15 |
| W2.13 | Insurance | ✓ | | ✓ | | Accounts Manager | W2.5 | 7 |
| W2.14 | Other statutory requirements | | | ✓ | | CH Department Head (Legal) | W2.5 | 15 |

**W2.9–W2.14 are rule-driven.** They exist in the template as placeholders; the actual set is
resolved per store by `CH Store Opening Requirement Rule` on `applies_to_state` /
`applies_to_city` / `applies_to_brand` / `applies_to_store_format`. A store in a state where
Shops & Establishments registration is handled centrally gets W2.10 removed at creation, not
marked N/A afterwards.

W2.5 creates a `CH Lease` (ch_assets, submittable, Ind AS 116) with `security_deposit` and
`commencement_date`. The lease document is **not** re-modelled in the project — the task links it.

## 19.4 W3 — Design & Civil Work (19)

| ID | Task | M | ★ | E | A | Owner | Dep | D |
|---|---|:-:|:-:|:-:|:-:|---|---|--:|
| W3.1 | Site measurement | ✓ | | ✓ | | CH Projects Team Member | W2.5 | 2 |
| W3.2 | Layout planning | ✓ | | ✓ | | CH Projects Team Member | W3.1 | 5 |
| W3.3 | **Design approval** | ✓ | ★ | ✓ | ✓ | CH Project Manager | W3.2 | 3 |
| W3.4 | BOQ preparation | ✓ | | ✓ | | CH Projects Team Member | W3.3 | 5 |
| W3.5 | Contractor finalisation | ✓ | | ✓ | ✓ | Purchase Manager | W3.4 | 7 |
| W3.6 | Civil work | ✓ | | ✓ | | CH Projects Team Member | W3.5 | 20 |
| W3.7 | Electrical work | ✓ | | ✓ | | CH Projects Team Member | W3.6 | 10 |
| W3.8 | Plumbing | ✓ | | | | CH Projects Team Member | W3.6 | 7 |
| W3.9 | Flooring | ✓ | | | | CH Projects Team Member | W3.7 | 7 |
| W3.10 | Painting | ✓ | | | | CH Projects Team Member | W3.9 | 5 |
| W3.11 | Ceiling | ✓ | | | | CH Projects Team Member | W3.7 | 5 |
| W3.12 | Carpentry | ✓ | | | | CH Projects Team Member | W3.10 | 10 |
| W3.13 | Storefront & signage | ✓ | | ✓ | | CH Projects Team Member | W3.10 | 7 |
| W3.14 | Furniture & fixtures | ✓ | | | | CH Projects Team Member | W3.12 | 5 |
| W3.15 | Lighting | ✓ | | | | CH Projects Team Member | W3.11 | 3 |
| W3.16 | Air conditioning | ✓ | | ✓ | | CH Projects Team Member | W3.11 | 5 |
| W3.17 | Fire & safety equipment | ✓ | | ✓ | | CH Projects Team Member | W3.15 | 3 |
| W3.18 | Snag inspection | ✓ | | ✓ | | CH Project Manager | W3.14,W3.16,W3.17 | 2 |
| W3.19 | Rectification + **final interior approval** | ✓ | ★ | ✓ | ✓ | CH Project Manager | W3.18 | 5 |

W3.8, W3.9, W3.11 run in **parallel** with W3.7's successors — no dependency row between them.
This is what §16.4's "tasks that can run in parallel" means in practice: parallelism is the
*absence* of a `Task Depends On` row, not a flag.

## 19.5 W4 — IT & Technology (26)

| ID | Task | M | ★ | E | A | Owner | Dep | D |
|---|---|:-:|:-:|:-:|:-:|---|---|--:|
| W4.1 | Internet connection | ✓ | | ✓ | | CH IT Team | W3.7 | 15 |
| W4.2 | Backup internet connection | ✓ | | ✓ | | CH IT Team | W3.7 | 15 |
| W4.3 | Network cabling | ✓ | | | | CH IT Team | W3.7 | 3 |
| W4.4 | Firewall / router | ✓ | | | | CH IT Team | W4.1,W4.3 | 2 |
| W4.5 | Wi-Fi | ✓ | | | | CH IT Team | W4.4 | 1 |
| W4.6 | CCTV | ✓ | | ✓ | | CH IT Team | W4.3 | 3 |
| W4.7 | NVR / storage | ✓ | | | | CH IT Team | W4.6 | 1 |
| W4.8 | Access control | | | | | CH IT Team | W4.3 | 2 |
| W4.9 | Attendance device | ✓ | | | | CH IT Team | W4.3 | 1 |
| W4.10 | Computers / laptops | ✓ | | | | CH IT Team | W4.3 | 2 |
| W4.11 | Printers | ✓ | | | | CH IT Team | W4.3 | 1 |
| W4.12 | Barcode scanners | ✓ | | | | CH IT Team | W4.10 | 1 |
| W4.13 | POS terminals | ✓ | | | | CH IT Team | W4.10 | 2 |
| W4.14 | Payment terminals | ✓ | | ✓ | | CH IT Team | W4.13 | 5 |
| W4.15 | UPS / inverter | ✓ | | | | CH IT Team | W3.7 | 2 |
| W4.16 | Telephony | | | | | CH IT Team | W4.1 | 2 |
| W4.17 | Email & user accounts | ✓ | | | | CH IT Team | W6.4 | 2 |
| W4.18 | **ERPNext user creation** | ✓ | | | | System Manager | W4.17,W6.4 | 1 |
| W4.19 | **Branch setup** | ✓ | | | | System Manager | W1.9 | 1 |
| W4.20 | **Warehouse setup** | ✓ | ★ | | | System Manager | W4.19 | 1 |
| W4.21 | **Cost-centre setup** | ✓ | | | | Accounts Manager | W4.19 | 1 |
| W4.22 | **POS Profile setup** | ✓ | | | | System Manager | W4.20,W4.21 | 1 |
| W4.23 | Tax & accounting configuration | ✓ | | | ✓ | Accounts Manager | W4.21 | 2 |
| W4.24 | Stock access & permissions | ✓ | | | | System Manager | W4.18,W4.20 | 1 |
| W4.25 | GoFix application setup | | | | | CH IT Team | W4.22 | 2 |
| W4.26 | Website / store-locator setup, monitoring, security validation, data & billing test, **IT go-live sign-off** | ✓ | ★ | ✓ | ✓ | CH IT Team | W4.14,W4.22,W4.24 | 3 |

**W4.19–W4.22 and W4.18/W4.24 are automation-backed, not manual.** Completing them calls the
existing idempotent provisioners and writes a `CH Store Opening Provision Log` row:

| Task | Calls | Creates |
|---|---|---|
| W4.19 | `ch_projects.provision.ensure_branch` | `Branch` — **pending the §17.6 decision** |
| W4.20 | `ch_item_master...ch_store.ensure_store_bins` | store `Warehouse` tree |
| W4.21 | `ch_item_master...ch_store.ensure_store_cost_center_hierarchy` | region + store `Cost Center` |
| W4.22 | `ch_item_master...ch_store.ensure_store_pos_profile` | `POS Profile` named `POS - {store_code}` |
| — (stage 9 entry) | `ch_store._generate_prefixed_store_code` + `next_free_numeric_id("store")` | **store code + store id** |
| W4.23 | `ch_erp15.accounts_setup.setup_company_accounts` | company default **accounts**, MOP wiring, tax templates |
| W4.18 | `ch_erp15.ch_erp15.onboarding` + `CH User Scope` | `User` + scope rows |

The task's completion is the **trigger**; the provisioner is the **implementation**; the log row
is the **proof**. Re-running is a no-op that returns the existing document, because the log has a
unique index on (`store_opening`, `action`) and every provisioner is already existence-checked.

## 19.6 W5 — Procurement & Assets (17)

| ID | Task | M | ★ | E | A | Owner | Dep | D |
|---|---|:-:|:-:|:-:|:-:|---|---|--:|
| W5.1 | Material requirement | ✓ | | | | CH Projects Team Member | W3.4 | 3 |
| W5.2 | Vendor identification | ✓ | | | | Purchase Manager | W5.1 | 5 |
| W5.3 | RFQ | ✓ | | | | Purchase Manager | W5.2 | 5 |
| W5.4 | Quotation comparison | ✓ | | ✓ | | Purchase Manager | W5.3 | 3 |
| W5.5 | **Purchase approval** | ✓ | ★ | | ✓ | CH Store Opening Approver | W5.4 | 3 |
| W5.6 | Purchase Order | ✓ | | | | Purchase Manager | W5.5 | 1 |
| W5.7 | Delivery tracking | | | | | Purchase Manager | W5.6 | 15 |
| W5.8 | Material receipt | ✓ | | | | CH Projects Team Member | W5.7 | 2 |
| W5.9 | Quality verification | ✓ | | ✓ | | CH Projects Team Member | W5.8 | 2 |
| W5.10 | Supplier invoice | ✓ | | | | Accounts Manager | W5.9 | 3 |
| W5.11 | Payment tracking | | | | | Accounts Manager | W5.10 | 15 |
| W5.12 | Asset creation | ✓ | | | | Accounts Manager | W5.9 | 1 |
| W5.13 | Asset tagging | ✓ | | ✓ | | CH Projects Team Member | W5.12 | 2 |
| W5.14 | Asset assignment | ✓ | | | | CH Projects Team Member | W5.13 | 1 |
| W5.15 | Warranty / AMC details | | | ✓ | | CH Projects Team Member | W5.12 | 2 |
| W5.16 | Installation | ✓ | | ✓ | | CH Projects Team Member | W5.9 | 5 |
| W5.17 | **Asset handover** | ✓ | ★ | ✓ | ✓ | Operations Manager | W5.14,W5.16 | 1 |

Documents are ERPNext's own: `Material Request` → `Request for Quotation` → `Supplier Quotation`
→ `Purchase Order` → `Purchase Receipt` → `Purchase Invoice` → `Payment Entry`, all carrying
`project`. `Asset` is created by ERPNext from the Purchase Receipt for fixed-asset Items;
`ch_project` and `ch_store` are stamped in `Asset.validate`. Asset tagging reuses `ch_assets`'s
`Asset.ch_asset_tag` and its duplicate-tag guard. **No procurement or asset data is copied onto
the project.**

W5.5 does not replace the `Budget` control: `Budget` with `budget_against = Project` and
`action_if_annual_budget_exceeded = Stop` blocks an over-budget PO at submit, independently of
this task.

## 19.7 W6 — People & Operations (15)

| ID | Task | M | ★ | E | A | Owner | Dep | D | ch_hrms record |
|---|---|:-:|:-:|:-:|:-:|---|---|--:|---|
| W6.1 | Manpower planning | ✓ | | | ✓ | HR Manager | W1.8 | 5 | `CH Workforce Plan Line` (`ch_store`) |
| W6.2 | Recruitment request | ✓ | | | | HR Manager | W6.1 | 2 | `CH Candidate` pipeline |
| W6.3 | **Store manager appointment** | ✓ | ★ | ✓ | ✓ | HR Manager | W6.2 | 20 | `Employee` + `CH Offer Approval` |
| W6.4 | Staff recruitment | ✓ | | | | HR Manager | W6.2 | 30 | `CH Candidate` |
| W6.5 | Employee onboarding | ✓ | | | | CH People Admin | W6.4 | 5 | `CH Onboarding Journey` (`ch_store`) |
| W6.6 | Training | ✓ | | ✓ | | CH Talent Partner | W6.5 | 10 | `CH Training Session` |
| W6.7 | Uniforms | | | | | CH People Admin | W6.4 | 10 | procurement task |
| W6.8 | Attendance setup | ✓ | | | | CH People Admin | W4.9,W6.5 | 1 | HRMS `Shift Type` |
| W6.9 | Rosters | ✓ | | | | CH Store Executive | W6.5 | 2 | `CH Shift Coverage Plan` (`ch_store`) |
| W6.10 | SOP training | ✓ | | ✓ | | CH Talent Partner | W6.5 | 3 | `CH Course Enrollment` |
| W6.11 | Cash-handling training | ✓ | | ✓ | | Accounts Manager | W6.5 | 2 | `CH Course Enrollment` |
| W6.12 | Product training | ✓ | | ✓ | | CH Talent Partner | W6.5 | 3 | `CH Course Enrollment` |
| W6.13 | Repair / service training | | | ✓ | | CH Talent Partner | W6.5 | 3 | `CH Course Enrollment` |
| W6.14 | Emergency contacts | ✓ | | ✓ | | CH Store Executive | W6.5 | 1 | — |
| W6.15 | **Opening-day staffing confirmation** | ✓ | ★ | | ✓ | Operations Manager | W6.9,W6.10,W6.11,W6.12 | 1 | `CH Shift Coverage Plan` |

**No headcount, joining date or training result is stored on the project.** Two readiness checks
read ch_hrms directly:

- `SO_STAFF_HEADCOUNT` (`hard=True`) — `Employee` count for the store ≥ Σ `budgeted_headcount`
  from `CH Workforce Plan Line` where `ch_store = <store>`.
- `SO_STAFF_ONBOARDING` (`hard=True`) — no `CH Onboarding Journey` for the store carrying an
  incomplete task with `blocks_activation = 1` (reusing ch_hrms's own flag).

## 19.8 W7 — Inventory & Merchandising (12)

| ID | Task | M | ★ | E | A | Owner | Dep | D |
|---|---|:-:|:-:|:-:|:-:|---|---|--:|
| W7.1 | Opening-stock plan | ✓ | | | ✓ | CH Area Sales Manager | W4.20 | 5 |
| W7.2 | Item assortment | ✓ | | | | Category Manager | W7.1 | 3 |
| W7.3 | Reorder levels | ✓ | | | | CH MRP Planner | W7.2 | 2 |
| W7.4 | Stock transfer | ✓ | | | | CH Hub Operator | W7.2 | 5 |
| W7.5 | Stock receipt | ✓ | | ✓ | | CH Store Executive | W7.4 | 3 |
| W7.6 | IMEI / serial-number verification | ✓ | | ✓ | | CH Store Executive | W7.5 | 2 |
| W7.7 | Shelf / display plan | ✓ | | ✓ | | CH Store Executive | W3.14 | 2 |
| W7.8 | Pricing labels | ✓ | | | | CH Store Executive | W7.5 | 2 |
| W7.9 | Promotional material | | | | | Marketing Manager | W7.7 | 3 |
| W7.10 | Visual merchandising | ✓ | | ✓ | | Marketing Manager | W7.7,W7.9 | 3 |
| W7.11 | Damaged / missing stock reconciliation | ✓ | | ✓ | | CH Store Executive | W7.6 | 2 |
| W7.12 | **Opening-stock sign-off** | ✓ | ★ | ✓ | ✓ | Operations Manager | W7.8,W7.10,W7.11 | 1 |

W7.4/W7.5 are ERPNext `Material Request` (with the existing `custom_store`) → `Stock Entry` →
`CH Transfer Manifest` (ch_logistics). Quantities live in `Bin`/SLE; the project counts documents,
not units.

## 19.9 W8 — Finance & Compliance (14)

| ID | Task | M | ★ | E | A | Owner | Dep | D |
|---|---|:-:|:-:|:-:|:-:|---|---|--:|
| W8.1 | Project budget | ✓ | ★ | | ✓ | Accounts Manager | W1.7 | 2 |
| W8.2 | Cost-centre activation | ✓ | | | | Accounts Manager | W4.21 | 1 |
| W8.3 | Cash / petty-cash setup | ✓ | | | | Accounts Manager | W8.2 | 2 |
| W8.4 | Bank / payment configuration | ✓ | | | ✓ | Accounts Manager | W8.2 | 3 |
| W8.5 | Tax configuration | ✓ | | | ✓ | Accounts Manager | W4.23 | 2 |
| W8.6 | Payment-mode configuration | ✓ | | | | Accounts Manager | W8.4,W4.22 | 1 |
| W8.7 | POS reconciliation test | ✓ | | ✓ | | Accounts Manager | W8.6 | 1 |
| W8.8 | Sample invoice | ✓ | | ✓ | | CH Store Executive | W8.6 | 1 |
| W8.9 | Sample return | ✓ | | ✓ | | CH Store Executive | W8.8 | 1 |
| W8.10 | Sample exchange | ✓ | | ✓ | | CH Store Executive | W8.8 | 1 |
| W8.11 | Sample repair invoice | | | ✓ | | CH Store Executive | W8.8 | 1 |
| W8.12 | Accounting-entry validation | ✓ | | ✓ | | Accounts Manager | W8.8,W8.9,W8.10 | 2 |
| W8.13 | Insurance & compliance documents | ✓ | | ✓ | | CH Department Head (Legal) | W2.13 | 2 |
| W8.14 | **Finance go-live approval** | ✓ | ★ | | ✓ | Finance Manager | W8.7,W8.12,W8.13 | 2 |

W8.1 creates the `Budget` with `budget_against = Project`,
`applicable_on_purchase_order = 1` and `action_if_annual_budget_exceeded = Stop`. That single
record is the §16.7 "prevent POs exceeding approved limits" control — **no custom code**.

W8.8–W8.11 are real POS transactions in the new store, reversed after validation. Their evidence
is the invoice name plus the GL screenshot, held in the document register.

## 19.10 W9 — Marketing & Launch (13)

| ID | Task | M | ★ | E | A | Owner | Dep | D |
|---|---|:-:|:-:|:-:|:-:|---|---|--:|
| W9.1 | Store branding | ✓ | | ✓ | | Marketing Manager | W3.13 | 5 |
| W9.2 | Outdoor signage | ✓ | | ✓ | | Marketing Manager | W3.13 | 5 |
| W9.3 | Google Business Profile | ✓ | | ✓ | | Marketing Manager | W2.5 | 3 |
| W9.4 | Website listing | ✓ | | | | Marketing Manager | W4.26 | 2 |
| W9.5 | Store locator | ✓ | | | | CH IT Team | W4.26 | 1 |
| W9.6 | Social-media announcement | | | | | Marketing Manager | W9.3 | 2 |
| W9.7 | Local marketing | | | | | Marketing Manager | W9.1 | 5 |
| W9.8 | Launch campaign | | | ✓ | ✓ | Marketing Manager | W9.7 | 7 |
| W9.9 | Invitations | | | | | Marketing Manager | W9.8 | 3 |
| W9.10 | Opening event | | ★ | ✓ | | Marketing Manager | W9.9 | 1 |
| W9.11 | Promotional offers | | | | ✓ | CH Offer Manager | W9.8 | 3 |
| W9.12 | Photography / video | ✓ | | ✓ | | Marketing Manager | W9.10 | 1 |
| W9.13 | Customer communication | | | | | Marketing Manager | W9.8 | 2 |

W9.5 uses the existing store-locator/geocoding path (`sync_store_geo_to_warehouses`); geo lives
on `CH Store`, not on `Warehouse`. W9.12's output is filed as
`CH Store Opening Document` of category **Photograph** — §16.1's "store-opening photographs".

## 19.11 W10 — Go-Live & Handover (25)

| ID | Task | M | ★ | E | A | Owner | Dep | D |
|---|---|:-:|:-:|:-:|:-:|---|---|--:|
| W10.1 | Final site inspection | ✓ | | ✓ | | CH Project Manager | W3.19 | 1 |
| W10.2 | Snag-list closure | ✓ | | ✓ | | CH Project Manager | W10.1 | 3 |
| W10.3 | Safety check | ✓ | | ✓ | ✓ | CH Department Head (Safety) | W3.17,W10.2 | 1 |
| W10.4 | Legal / compliance check | ✓ | | ✓ | ✓ | CH Department Head (Legal) | W2.11,W8.13 | 1 |
| W10.5 | Asset verification | ✓ | | ✓ | | Accounts Manager | W5.17 | 1 |
| W10.6 | Opening-stock verification | ✓ | | ✓ | | Operations Manager | W7.12 | 1 |
| W10.7 | POS test | ✓ | | ✓ | | CH IT Team | W4.26 | 1 |
| W10.8 | Payment test | ✓ | | ✓ | | Accounts Manager | W4.14,W8.6 | 1 |
| W10.9 | Invoice test | ✓ | | ✓ | | Accounts Manager | W8.8 | 1 |
| W10.10 | Return / exchange test | ✓ | | ✓ | | Accounts Manager | W8.9,W8.10 | 1 |
| W10.11 | Repair-ticket test | | | ✓ | | CH IT Team | W4.25 | 1 |
| W10.12 | User-access test | ✓ | | ✓ | | System Manager | W4.18,W4.24 | 1 |
| W10.13 | Internet / failover test | ✓ | | ✓ | | CH IT Team | W4.1,W4.2 | 1 |
| W10.14 | CCTV test | ✓ | | ✓ | | CH IT Team | W4.6,W4.7 | 1 |
| W10.15 | Staff-readiness confirmation | ✓ | | | ✓ | Operations Manager | W6.15 | 1 |
| W10.16 | **Department sign-offs** | ✓ | ★ | | ✓ | CH Department Head | W10.3…W10.15 | 2 |
| W10.17 | **Management approval to open** | ✓ | ★ | | ✓ | CH Store Opening Approver | W10.16 | 1 |
| W10.18 | **Go / no-go decision** | ✓ | ★ | | ✓ | CH Store Opening Approver | W10.17 | 1 |
| W10.19 | **Store opening** | ✓ | ★ | ✓ | | Operations Manager | W10.18 | 1 |
| W10.20 | Projects Team → Operations handover | ✓ | ★ | ✓ | ✓ | CH Project Manager | W10.19 | 2 |
| W10.21 | Post-opening support | ✓ | | | | CH Project Manager | W10.19 | 30 |
| W10.22 | Post-opening review | ✓ | | ✓ | | CH Project Manager | W10.21 | 3 |
| W10.23 | Budget reconciliation | ✓ | | ✓ | ✓ | Accounts Manager | W10.21 | 3 |
| W10.24 | Asset & document reconciliation | ✓ | | ✓ | | Accounts Manager | W10.21 | 2 |
| W10.25 | **Project closure** | ✓ | ★ | | ✓ | CH Store Opening Approver | W10.22,W10.23,W10.24 | 1 |

**W10.18 is not a person's opinion.** The go/no-go panel publishes `blocker_count` from the
readiness run; the transition to *Ready to Open* is refused while it is non-zero, whatever the
readiness percentage reads. That is §16.6's anti-average clause made structural.

## 19.12 Template summary

> **Subjects must be unique within a workstream.** W1.7 and W10.17 were both
> called "Management approval" in the first draft; the seeder keys an existing
> template task on its subject, so the second silently reused the first and the
> library came out one task short with no error anywhere. They are now
> "Management approval of business case" and "Management approval to open", and
> the seeder keys on subject **within the parent group** so the collision cannot
> recur across workstreams.

| Workstream | Tasks | Mandatory | Milestones | Evidence | Approval |
|---|--:|--:|--:|--:|--:|
| W1 Business Approval & Planning | 9 | 8 | 3 | 6 | 2 |
| W2 Property & Legal | 14 | 13 | 1 | 12 | 4 |
| W3 Design & Civil Work | 19 | 19 | 2 | 12 | 3 |
| W4 IT & Technology | 26 | 23 | 2 | 5 | 2 |
| W5 Procurement & Assets | 17 | 14 | 2 | 6 | 2 |
| W6 People & Operations | 15 | 13 | 2 | 7 | 3 |
| W7 Inventory & Merchandising | 12 | 11 | 1 | 6 | 2 |
| W8 Finance & Compliance | 14 | 13 | 2 | 7 | 4 |
| W9 Marketing & Launch | 13 | 6 | 1 | 6 | 2 |
| W10 Go-Live & Handover | 25 | 24 | 6 | 19 | 9 |
| **Total** | **164** | **144** | **22** | **86** | **33** |

> Counted from the shipped library (`ch_projects/setup/task_library.py`), not by
> hand. An earlier draft of this table was totalled manually and was wrong in
> four columns; these are the numbers the seeder actually produces.

**Longest path: 190 calendar days** from project start to `W10.25 Project closure`, computed by
`task_library.compute_starts()` rather than estimated. The chain to the store actually opening
(`W10.19`) is **165 days**; the remaining 25 are the stabilisation window and closure, which run
after the store is trading.

That number matters at approval time. `Project.expected_end_date` is set to the **later** of the
committed opening date and the plan's own span, because ERPNext refuses a task that starts after
its project ends — so a 120-day commitment cannot silently hold a 165-day plan. Taking the later
of the two keeps the plan intact and lets `opening_date_at_risk` say, correctly and on day one,
that the committed date does not fit. Shortening the plan to fit the promise would hide exactly
what the Projects Team needs to see.

Every other workstream carries float against that chain — which is what `ch_total_float` computes
and what the opening-date-at-risk warning compares against `approved_opening_datetime`.

**Set `Project.percent_complete_method = "Task Weight"`** and weight the group tasks by
workstream. The default (Task Completion) would let 26 IT tasks outweigh a single fire NOC.
Weighting improves the *number*; only `blocker_count` actually gates the gate.

## 19.13 Workspace: "New Store Projects" (§16.11)

| Element | Type | Source |
|---|---|---|
| Active store openings | Shortcut → `CH Store Opening` | `stage not in (Closed, Cancelled)` |
| Upcoming opening dates | Number Card | count where `approved_opening_datetime` within 30 days |
| Overall readiness | Chart (bar by project) | `readiness_percent` |
| Projects at risk | Number Card | `opening_date_at_risk = 1` |
| Critical blockers | Number Card | Σ `blocker_count` |
| Overdue tasks | Number Card | `Task` status Overdue, `ch_store_opening` set |
| Pending approvals | Number Card | `ch_approval_status = Pending` |
| Department-wise completion | Chart | `Task` grouped by `department` |
| Budget vs actual | Chart | `Budget` vs `Project.total_purchase_cost` |
| Procurement delays | Number Card | PO past `schedule_date` with no PR |
| IT readiness | Number Card | readiness rows where `category = IT` |
| Store-opening calendar | Calendar view | `CH Store Opening.approved_opening_datetime` |
| Recently completed | Shortcut | `stage = Closed`, last 90 days |
| Post-opening issues | Shortcut → `Issue` | `ch_store_opening` set, status Open |

> Two workspace gotchas already paid for on this bench: the JSON is **skipped on migrate unless
> its `modified` is newer than the DB row** (ship forward-dated or force-import); and shortcut
> `stats_filter` fieldnames are **never validated** — a wrong one fails at runtime on the
> workspace, not at build. Icons: workspace = *timeless*, sidebar = *lucide*.

## 19.14 Reports (§16.11) — all 17

| # | Report | Type | Primary source |
|---|---|---|---|
| 1 | Project portfolio status | Query | `CH Store Opening` |
| 2 | Store openings by stage | Query | `CH Store Opening` |
| 3 | Tasks by department | Query | `Task` + `department` |
| 4 | Tasks by owner | Query | `Task` + `ToDo` |
| 5 | Overdue tasks | Query | `Task` |
| 6 | Critical-path delays | **Script** | `Task` + float computation |
| 7 | Pending approvals | Query | `Task.ch_approval_status` + `CH Store Opening Signoff` |
| 8 | Opening-date variance | Query | `proposed` vs `approved` vs `CH Store.opening_date` |
| 9 | Budget variance | **Script** | `Budget` + PO/PI/PE aggregation |
| 10 | Procurement status | Query | MR → RFQ → SQ → PO → PR → PI by project |
| 11 | Vendor delay report | **Script** | PO `schedule_date` vs PR `posting_date` |
| 12 | Missing compliance documents | **Script** | `CH Store Opening Document Type` × register |
| 13 | Readiness score | **Script** | readiness engine |
| 14 | Project closure exceptions | **Script** | closure gates that failed |
| 15 | Planned vs actual opening dates | Query | `CH Store Opening` + `CH Store` |
| 16 | Average store-opening lead time | **Script** | `CH Store Opening Closure.lead_time_days` |
| 17 | Repeated causes of delay | Query | `CH Store Opening Delay Cause` — a `GROUP BY`, because the cause is a **master link, not free text** |

> Report gotchas from this bench: never put `&` in a Report name (it breaks the route);
> `frappe.get_list` **rejects SQL functions in `order_by`**; and a report must be executed as a
> **non-bypass user** to prove its scope filter binds — importing the scope helper only proves it
> was imported. Eight report-scope leaks were found here that way before.
