# 12 — Change Log

Every entry: reason, affected flow, files changed, database impact, backward-compatibility
impact, test evidence. Newest first.

---

## CHG-006 — `ch_projects`: store-opening project management, built and installed

**Reason (user request).** Reports `14`–`20` recommended Option C: a dedicated app on an
ERPNext Project/Task foundation. This is that build, through phase 3 of the sequence in
report `16 §16.7` — proposal, stage workflow, task library, task gates, readiness gate,
critical path, and master provisioning.

**Affected flow.** New. Nothing existing changes behaviour: every control is scoped to
documents that carry `Task.ch_store_opening`, and the Projects module had **0 rows** before
this (report `14 §14.3`), so there is no population to regress.

### What shipped

| Area | Content |
|---|---|
| App | `apps/ch_projects` — `required_apps = ["frappe", "erpnext"]`, real dependencies asserted in `before_install` (the `ch_assets`/`ch_hrms` pattern, to stay out of ch_erp15's `required_apps` cycle) |
| DocTypes | 17 — `CH Store Opening` (submittable) + 6 masters + 10 child tables |
| Custom fields | 47 across 9 doctypes; `Task.ch_budgeted_cost` and `ch_supplier` at **permlevel 1** |
| Roles | 8 new `CH ` roles; Project/Task access granted as **Custom DocPerm** |
| Workflow | 16 states, 41 transitions, `override_status = 1` |
| Task library | 164 detail tasks + 10 workstream groups, 205 dependencies, `Project Template` |
| Readiness | 28 checks registered into **ch_erp15's existing** `closure/readiness.py` — no second engine |
| Provisioning | 8 idempotent actions delegating to `ch_store.py` / `accounts_setup.py` |
| Indexes | 5 performance + **1 unique** `(store_opening, action)` on the provision log |
| Reports | **17 Script Reports**, 63 filters. Script rather than Query on purpose: a Query Report runs SQL with no permission layer, and this estate has shipped eight report-scope leaks |
| Desk | "New Store Projects" workspace (13 cards, 64 links, 7 shortcuts, 5 number cards), a 65-item Workspace Sidebar, and a `/desk` tile. All 9 master DocTypes and all 17 reports reachable; 0 broken links |
| UI | **Store Projects SPA** at `/store-projects` — Vue 3 + frappe-ui + Tailwind, built with Vite. Role-driven menu, company/state/city/format/stage filters, portfolio and go/no-go screens |
| Tests | 64, in five suites |

### Twelve defects found by building it, and fixed

1. **`developer_mode` wrote CH roles into ERPNext's `task.json` and `project.json`.** Appending
   to `DocType.permissions` and saving persists the DocType to disk on a developer_mode site —
   into the *vendor* app. Both files were reverted; permissions now go through
   `frappe.permissions.add_permission`, which writes Custom DocPerm and touches no vendor file.
   `clear_legacy_docperms()` removes the rows the first install left behind.
2. **Two library tasks shared the subject "Management approval"** (W1.7 and W10.17). The seeder
   keys an existing template task on its subject, so the second silently reused the first: the
   library came out one task short, with the go-live approval missing and no error anywhere.
   Renamed, and the seeder now keys on subject **within the parent group**.
3. **A duplicated workflow made `stage_index` report 17 for Draft.** `install_workflow(force=True)`
   is self-healing; `stage_index` now takes `MIN(idx)` so a duplicate cannot produce nonsense.
4. **Readiness sections were seeded with no company and silently acquired one.**
   `Document.insert()` applies session defaults, so the "global" set belonged to the installing
   user's company — and a proposal for any other company seeded **zero** sign-off rows, which is
   indistinguishable from sections nobody has approved. Compounded by `NULL IN (...)` never
   matching in SQL. Now seeded per company, with a single NULL-safe `sections_for()` lookup.
5. **A failing provisioning step lost its own log row.** The rollback that undid the step's
   partial work also undid the claim row, leaving a failure with no audit record. The claim now
   commits before the step runs.
6. **The parity checker false-positived on `Task.ch_checklist`.** A `Table` field has no column,
   so `has_column` reports it missing forever — a check nobody would keep reading.
8. **Deleting a Workspace on a `developer_mode` site deletes its source JSON from disk**, and
   that deletion is not transactional. `import_file_by_path(force=True)` deletes then re-inserts,
   so a rollback anywhere in the same transaction restored the database row and left the fixture
   permanently gone — the app's workspace vanished the first time the suites were run together,
   and the only symptom was an empty page. `install_workspace()` now reads the JSON and upserts
   the document; it never deletes.
9. **A rollback-based teardown leaves orphaned document locks.** Frappe releases a submit lock
   when the transaction ends normally; `frappe.db.rollback()` never gets there. The next suite to
   touch the document died with `DocumentLockedError` on a document nothing was using — and it
   looked like *that* suite's bug. `tests/release_stale_locks()` is called from every teardown,
   so the order suites run in cannot change whether they pass.
12. **A package silently shadowed a module of the same name.** Adding `ch_projects/api/` for the
   new UI endpoints made the existing `ch_projects/api.py` unreachable — Python resolves the
   package first — so seven whitelisted endpoints ceased to exist with no import error and nothing
   in any log. The only symptom was a user clicking a button. Fixed by moving it to
   `api/actions.py` and re-exporting, which keeps the public dotted paths. `test_api_surface.py`
   now scrapes endpoint paths out of the **built front end** and asserts each resolves and is
   whitelisted, and checks structurally for any other `foo.py` beside a `foo/`.
11. **ERPNext v16 flattened `Budget`.** `account` and `budget_amount` are fields on the document;
   in v15 they were rows in a `Budget Account` child table. That table survives a migration as an
   empty husk, so the aggregation written against it did not error — it silently returned **zero**,
   and every budget figure in the app read as "no budget" with nothing in any log. Found only
   because the demo data made a real budget and the screen still showed nothing.
10. **A scoped user was offered the entire 815-row city master.** The filter only narrowed cities
   when an explicit city grant existed; a company-only grant fell through to everything. Now the
   cities are those the user's companies actually operate in.
7. **Every `CH Store Opening Document Type` was silently scoped to one company** — the same
   session-defaults trap as (4), on a field where blank means "applies everywhere". The
   consequence was severe: an opening in any *other* company required **no** go-live documents
   at all, so the document gate passed vacuously. Found by a report returning zero rows where it
   should have returned twenty-six. Types are now forced global on seed, a repair unscopes the
   40 existing rows, and a test asserts a non-empty required set **per company** — because the
   bug is invisible from whichever company happens to be first.

### Three ERPNext constraints the design had to answer

- **`Project.copy_from_template()` does not copy custom fields.** Without
  `propagate_template_fields()` every cloned task arrives with `ch_is_mandatory` unset and the
  go-live gate has nothing to block on — silent and total. Asserted by test, not assumed.
- **`Task.validate_parent_expected_end_date`** refuses a child that outlasts its parent group,
  and **`validate_parent_project_dates`** refuses a task starting after the project ends. Group
  tasks are now dated to span their children, and `Project.expected_end_date` is the **later** of
  the committed opening date and the plan's own 190-day span — so a 120-day commitment holding a
  165-day plan reads as *at risk* on day one instead of failing to save.
- **There are zero Holiday Lists on this site and no company default**, and
  `update_if_holiday()` throws without one. An empty `Store Opening Calendar - <abbr>` is created
  for the project and is deliberately **not** set as the company default.

### Database impact

Additive only. 17 new tables, 47 custom fields, 8 roles, 28 Custom DocPerm rows, 1 workflow,
174 template `Task` rows, 1 `Project Template`, 119 configuration master rows (5 store formats,
40 document types, 56 readiness sections, 18 delay causes), 6 indexes. One
`Holiday List` and one `Region - Chennai - BM` cost-centre group node were created by the test
runs and left in place — both are shared infrastructure the estate creates anyway.

**Backward compatibility:** no standard DocType JSON modified (`git status` clean on frappe,
erpnext and hrms); no `override_doctype_class` added, preserving this bench's zero-collision
record; ERPNext's `reschedule_dependent_tasks` is suppressed by capture-and-restore for
store-opening tasks only.

### Test evidence

`ch_projects/tests/` — **30 tests, 30 passing**, loaded with `unittest.TestLoader` in
`bench console` (`bench run-tests --module` reports zero tests on this bench, which looks like a
pass and is not one).

| Suite | Tests | Covers |
|---|--:|---|
| `test_store_opening_lifecycle.py` | 17 | TC-01…TC-10, TC-21 — proposal, rejection creating no Project, template + custom-field propagation, finish-to-start, parallel tasks, critical path and float, no-silent-reschedule, evidence/checklist/approval gates, readiness |
| `test_provisioning.py` | 13 | TC-16…TC-22, TC-29 — store code, accounts, cost centre, warehouse, POS profile, branch, duplicate prevention, the unique index enforced by the **database**, go-live refused at 62% readiness, scope as a non-bypass user |
| `test_desk.py` | 9 | workspace/sidebar/tile exist, every link resolves, every master DocType is reachable, number cards actually execute, icons come from the right set (timeless vs lucide), install is idempotent |
| `test_ui_context.py` | 9 | the server-side answer that drives the UI: a limited role gets a smaller menu, **every nav item survives a real `has_permission` check for the doctype and ptype the screen will use**, filters offer only in-scope values, capabilities are narrowed, and an unscoped user sees nothing rather than everything |
| `test_reports.py` | 16 | all 17 execute with data **and on an empty portfolio**, all are Script Reports, no name contains `&`, content assertions on nine of them, and **every report returns zero rows to a non-bypass user while returning rows to Administrator in the same run** |

`after_migrate` run twice back to back creates nothing on the second pass. Field parity: 47
declared, 0 missing. No `Error Log` rows from a passing run. Site left clean — 0 store openings,
0 projects, CH Store back to 56, Warehouse back to 431.

### CHG-008 — In-app create and edit

Both front ends used to send the user to the desk form for every create and edit, which made them
two applications wearing one coat. `ch_erp15/ui_forms.py` renders a form from Frappe's own
metadata and writes through the ordinary document API, and the kit gained `DocForm` and `Drawer`
so a create never costs the user their place in the list behind it.

Three properties make a generic write endpoint defensible rather than reckless, and each is
asserted by `test_ui_forms.py` (12 tests) rather than assumed:

* **An allowlist per app** — `ch_ui_editable_doctypes` in each app's hooks. `User`, `Role` and
  `System Settings` are refused outright.
* **Writes run the real controller** — `get_doc().insert()` / `.save()`, so the city/state guard,
  the mandatory fields and the workflow guards all fire. A form that skipped them could create
  records the desk would have refused.
* **permlevel holds in both directions** — the financial fields are absent from a task owner's
  form spec, and a crafted payload carrying `budget_amount` does not land. A spec that merely
  marks a field read-only is a suggestion, and payloads ignore suggestions.

Table, Attach and HTML fields are deliberately not rendered; those link to the desk form, which is
honest about what it can edit rather than silently dropping a child table.

The Opening page also gained real workflow buttons, built from
`ui_forms.workflow_actions` — the transitions the *server* says are available, so a button can
never offer a move `apply_workflow` would refuse. Reasons are prompted for before the call, so the
user is not told "a reason is required" for a field they cannot see.

### CHG-007 — Asset Manager, and one shared UI kit

`/asset-hub` is a second Vue SPA, and the reason it exists is consistency: the Asset Hub was a
desk page of hand-written jQuery and CSS, and next to the new Store Projects UI it read as a
different product. Rather than give Assets a second bespoke front end — which would guarantee the
same complaint again — the shared components were lifted into
`ch_erp15/public/ui/` and **both** apps now render from them. An app supplies its identity through
`configureUi()`; the kit knows nothing about any one app.

The server side is almost entirely reuse. `ch_assets.api` already had context, filter options,
search, detail and a capabilities system, and `asset_hub_api.get_asset_hub_data` already returned
the whole dashboard. `ch_assets/ui_api.py` is an adapter onto the kit's contract plus six list
endpoints — scope, capabilities and the entry gate all stay where the desk and the existing
portal already enforce them.

Nine screens: Overview, Register, Verification, Maintenance, Transfers, Leases, Capex, Statutory,
Reports, plus asset detail. Measured live: 180 assets, ₹3.96cr gross block, 44% verification
coverage by value, 4 alerts.

Deliberately **not** a package named `ch_assets/api/`: `ch_assets/api.py` already exists, and that
is exactly the shadowing that broke `ch_projects.api` (defect 12 below).

### The UI layer

`/store-projects` is a Vue 3 SPA served by Frappe, built with `frappe-ui` — the same toolkit
behind Frappe CRM, Helpdesk and the `/hrms` PWA already installed on this bench. It talks to the
DocTypes over `/api/method`, so **DocPerm, permlevel, `permission_query_conditions` and
`has_permission` all still apply**: a modern front end here cannot see more than the desk would.

The navigation is generated by `ch_projects.api.ui.get_context`, which filters every item with a
real `frappe.has_permission` call against the doctype that item opens. A menu hidden in
JavaScript is decoration — the route and the API behind it are still there — so the menu is a
*rendering* of the server's answer, never a computation of its own. Measured: Administrator gets
12 nav items and 7 capabilities; a `CH Projects Team Member` gets 8 and 1.

**All thirteen screens are built**: Portfolio, Board, Calendar, My Work, Go-Live Readiness,
Approvals, Documents, Procurement, Budget, People, Reports, Setup, and the per-opening go/no-go
page. Ten endpoints in `ch_projects/api/screens.py` back them, each starting from
`visible_openings()` so scope is applied before anything is shaped.

Three of them are worth calling out. **My Work** asks the server whether the completion gate
would pass (`can_complete`) instead of guessing client-side, so the button is disabled with a
reason rather than pressed and thrown. **Approvals** merges task approvals, department sign-offs
and document verifications — three different tables — into one inbox, each routing back to its
own guard. **Budget** refuses without permlevel-1 read in the endpoint itself, because hiding a
screen in the nav is not a permission.

### Not yet built

The remaining SPA screens (board, calendar, my work, approvals, documents, procurement, budget,
people, reports, setup), the document-register and closure **desk flows** (the DocTypes, server logic and reports exist;
there is no guided UI for uploading or superseding a document, or for walking a closure), and the
**notification digest** — the code exists and the cron entries are declared, but neither has been
exercised end to end. `ch_projects` is also **not yet a git repository**; every other app on this
bench is one.

One deliberate scope note on the reports: `visible_openings()` resolves scope into a list of
names and the reports filter on `IN (...)`. That is correct and far harder to get wrong than a
hand-assembled WHERE clause repeated seventeen times, but it assumes a portfolio of tens rather
than thousands of openings. At a few hundred concurrent openings it should become a join.

## CHG-005 — Service Request numbering + the device-barcode collision that blocked intake

**Reason.** Intake was failing outright with *"Serial No MO/26083100001 does not belong to Item
…"*. Requested fix: number a Service Request from company code + state code + location code +
date + a daily counter, so it is always unique and never breaks.

### Root cause of the breakage (FINDING-SERIAL-001 — now fixed)

`ServiceRequest.get_next_barcode_sequence()` derived the next device barcode by scanning
**`tabService Request.serial_no`** — the *consuming* table — for the highest value of the day.
The barcodes themselves live in **`Serial No`**. So any barcode whose Service Request had been
deleted or cancelled was invisible to the scan: it returned nothing, the sequence restarted at
`1`, and the regenerated barcode collided with a live `Serial No` belonging to a different
item. `fetch_warranty_from_serial()` then threw and the ticket could not be created at all.

The `GET_LOCK` advisory lock around it never helped: it protected a number that was already
wrong before any race. Reproduced deterministically — a `Serial No` orphaned by an earlier
deleted ticket made every subsequent intake for that day fail.

### Changes

| File | Change |
|---|---|
| `service_request.py` | new `autoname()` / `build_service_request_number()` and code resolvers `_company_code`, `_gst_state_code`, `_store_code`, `_sanitise` |
| `service_request.py` | `next_free_barcode()` replaces the table scan: atomic `getseries` counter + existence check against `Serial No`; `get_next_barcode_sequence()` kept as a thin deprecated wrapper for external callers |

### The number

```
SR CC SS LLL YYMMDD NNNN   ->   SRGF331682608310001      (19 chars, fixed width)
```

**Delimiter-free on purpose.** The ticket number *is* the barcode stuck on the customer's
device, so it is a contiguous alphanumeric string: nothing for a scanner or a downstream
parser to trip over, and Code128-safe. Every segment is zero-padded to a constant width, so
`ServiceRequest.parse_number()` splits it back apart by offset with no separator —
`SRGF331682608310001` → `{company GF, state 33, store 168, date 2026-08-31, counter 1}`.
A legacy `SR-YYMMDD-####` name returns `{}` rather than being mis-parsed.

- **company** — `Company.abbr` (`GF`, `BM`). Never the company name, which changes.
- **state** — GST state number of the receiving store's address (`33` Tamil Nadu, `27`
  Maharashtra), falling back to the company address.
- **store** — `CH Store.store_id`, zero-padded to 3. The store's own code (`GF-ANNANAGAR`) is
  up to 22 characters and repeats the company abbreviation, so the numeric id keeps the ticket
  number speakable.
- **counter** — `frappe.model.naming.getseries` on the full prefix: an atomic
  `INSERT .. ON DUPLICATE KEY UPDATE` on `tabSeries`, scoped per company+state+store+day.

### Stick-on device label (Ops Hub)

Every ticket header now carries a **Label** button (`get_device_label` +
`_print_device_label`). It prints a 50×30 mm sticker for the handset: the ticket number as a
Code128 symbol, the human-readable number directly beneath it, then customer, phone, device,
IMEI and received date.

Bars are rendered **server-side** as a PNG through the shared
`ch_erp15.print_helpers.get_barcode_base64`. A browser-drawn barcode degrades to plain text
when the page reaches a printer, and an unreadable sticker on a customer's device is worse
than none — so when the PNG cannot be produced the label refuses to print and says why rather
than emitting a number no scanner can read. Copies are clamped to 1–10, and the print is held
until the image decodes so the sheet is never blank.

Naming runs *before* `validate()`, so the state code is read from the warehouse address
directly rather than from `self.state_code`, which is not populated yet on a new document.

**Degrades, never blocks.** Unresolvable company / state / store fall back to `XX` / `00` /
`000`. Uniqueness still holds because the counter is per-prefix. Verified: a warehouse with no
CH Store still produced `SR-GF-33-000-260831-0001`.

### Database impact

No schema change. New rows in `tabSeries`, one per company+state+store+day prefix.

### Backward compatibility

**Forward-only.** Existing tickets keep their `SR-YYMMDD-####` names — renaming live Service
Requests would rewrite every Sales Order, Invoice, Job Assignment and Custody Log pointing at
them for no operational gain. Verified `SR-260818-10547`, `SR-260828-16281`, `SR-260828-16275`
still resolve. `get_next_barcode_sequence` retains its old signature and return type.

### Test evidence — `test_sr_numbering`, 45 assertions, 0 failures

- a ticket can be raised at all, and its barcode belongs to **its own** device
- the number carries **no separators**, is exactly 19 chars and `isalnum()` (Code128-safe),
  with each segment checked against master data (`GF`, `33`, store id `168`)
- it **parses back apart** without a delimiter, and a legacy name is not mis-parsed
- the label is printable, carries a real barcode PNG encoding the ticket number, and clamps copies
- three consecutive tickets share a prefix and the counter strictly increases `1,2,3`
- **the old bug specifically**: deleting a ticket leaves its `Serial No` behind, and the next
  ticket reuses neither the number nor the barcode — intake succeeds where it previously threw
- a different store yields a different location segment (`167` vs `168`)
- a warehouse with no CH Store still numbers and creates
- historic names are untouched and still resolvable

Full regression after the change: **288 passed, 0 failed** across 10 suites.

---

## CHG-004 — Technician custody and time recording before the Assign stage

**Reason (user request).** Analysis, Solutions and Confirm are real technician work, but the
first `Job Assignment` was only created at the **Assign** stage. Until then nobody could say
whose desk a ticket was sitting on, and the hours spent diagnosing never reached technician
performance, costing or SLA. The ask: an "assign to technician" control in **POS intake** and
in the **Analysis** phase, so custody and time are captured from the moment the device is
taken in.

**Design decision — reuse, do not invent.** `Job Assignment` already models exactly this:
a `job_type` option of **Diagnosis**, `service_engineer`, `assignment_status`,
`start_datetime` / `end_datetime` / `actual_hours`, a `technician_audit` child table, and a
controller that opens/closes **GoFix Custody Log** periods on every status change. So no new
doctype and no new field: an early assignment is a `Job Assignment` with
`job_type="Diagnosis"`. A parallel "assigned_to" field on Service Request was rejected — it
would have created a second, unreconciled source of technician time.

Why not the existing `assign_technician()`: it **requires a Service Order** (raised only at
estimate confirmation) and at least one chosen solution, and it advances the ticket to the
**Repair** stage. None of that is true or wanted at Analysis.

**Affected flow.** Walk-in intake (POS) → Analysis → Solutions. Stage progression is
deliberately **unchanged**.

### Files changed

| File | Change |
|---|---|
| `gofix_services/page/gofix_ops_hub/gofix_ops_hub.py` | new `assign_diagnosis_technician`, `release_diagnosis_technician`, `get_diagnosis_assignment`, helpers `_active_diagnosis_assignment`, `_close_diagnosis_assignment`; `get_ticket_detail` now returns `diagnosis_assignment`; `confirm_analysis` releases the clock when Analysis ends |
| `gofix_services/page/gofix_ops_hub/gofix_ops_hub.js` | "Pending with" custody bar on the Analysis panel, technician picker + Assign/Reassign/Stop, server-synced live elapsed timer |
| `ch_pos/api/repair.py` | `create_service_intake_from_pos` accepts `diagnosis_technician` and opens the assignment after the SR is created |
| `ch_pos/public/js/pos_app/modules/repair/repair_workspace.js` | "Assign to Technician" select on the intake form, populated from `get_technicians_for_grade`, sent with the intake payload |

### Behaviour notes

- **Reuse lookup is keyed on `service_request`, not `service_order`.** At Analysis there is no
  Service Order, and the repair-stage helper's `{"service_order": None}` filter would match a
  NULL against every other unlinked assignment on the site.
- **Handover closes the previous period first**, so one technician never inherits another's
  minutes. The new assignment is stamped `assignment_type="Technician Changed"`.
- **No custody row is written by this code.** `JobAssignment` already opens/closes
  `GoFix Custody Log` on status change; writing one here would double-count hours.
- **Failures never void an intake.** In POS, an assignment error is logged and the Service
  Request still stands — the device is already at the counter.
- **Timer uses the server clock.** The server/browser offset is measured once at render, so a
  wrong workstation clock cannot inflate or hide a technician's time. One `setInterval`,
  cleared on any stage change; no request per tick. Elapsed time is rendered as text
  (`held HH:MM:SS`), not colour alone.

### Database impact

No schema change. New rows only: `Job Assignment` (`job_type="Diagnosis"`) and the
`GoFix Custody Log` periods its controller already maintained.

### Backward compatibility

Additive. `diagnosis_technician` is optional in the POS payload; omitting it reproduces the
previous behaviour exactly. Existing `job_type="Repair"` assignments and the Assign-stage flow
are untouched.

### Test evidence

`test_diag_assign` — **40 assertions, 0 failures**, run against live tickets
(`SR-260828-16281`, `SR-260818-10547`), which were left untouched and still submitted:

- assignment creates a Diagnosis JA with **no** Service Order, status `In Progress`, stamped `start_datetime`
- **stage is unchanged** by assigning (`analysis -> analysis`), decision not forced to `In Service`
- exactly **one** custody period opened — not double-counted
- re-assigning the same technician returns the same JA and creates no second row
- handover completes the first JA, stamps `end_datetime`, closes that technician's custody period, and marks the new one `Technician Changed`
- release stops the clock; releasing again is a safe no-op
- `confirm_analysis` is wired to release, and release banks the hours
- blank and unknown technicians are rejected

Full regression after the change: **243 passed, 0 failed** across 9 suites.

---

## CHG-003 — `Item.ch_model` was mandatory on alternate migrates (cross-app conflict)

**Reason.** Three test suites began crashing with
`MandatoryError: [Item, GFR-…]: ch_model`. `ch_item_master` declares the field `reqd: 0` and
documents that it is enforced conditionally server-side; `ch_erp15/ch_erp15/custom/item.json`
— a Customize Form export with `sync_on_migrate: 1` — carried the same field with `reqd: 1`
and re-asserted it on **every migrate**. The field therefore flipped between migrates, and
every programmatic Item creation that does not set a model broke, including GoFix's `GFR-*`
service items.

**Files changed.** `ch_erp15/ch_erp15/custom/item.json` (`ch_model.reqd` 1 → 0, with a comment
explaining why it must not be re-armed).

**Database impact.** `tabCustom Field` row `Item-ch_model` settles at `reqd=0`; verified stable
across a subsequent migrate.

**Backward compatibility.** Restores the owning app's documented intent. Conditional
enforcement in `ch_item_master.overrides.item` is unchanged.

**Test evidence.** The three crashing suites (`test_fix1`, `test_fix2`, `test_e2e`) returned to
green; full regression 243/243.

---

## CHG-002 — Orphaned document locks blocked `bench migrate`

**Reason.** `Document.queue_action` writes its lock file immediately but enqueues the job with
`enqueue_after_commit=True`. When a migrate aborts before that commit the job is discarded and
the lock file survives, so `check_if_locked` raises for the next three hours — one failed
migrate guarantees the next one fails. This bench ships Role Profile fixtures and
`Role Profile.on_update` calls `queue_action`, so every migrate re-takes those locks.

**Files changed.** `ch_erp15/setup.py` — `clear_orphaned_document_locks()` called first in
`before_migrate`.

**Deliberately narrow rule.** Locks are cleared **only when every RQ queue is empty and
nothing is started** — with idle queues nothing can be holding a document. `bench_migrate.lock`
is never touched. If any queue has depth the function does nothing and lets the workers finish.

**Test evidence.** A migrate that had been failing on 8 pre-existing locks completed `exit=0`.
Later verified the guard **correctly declines** while a 479-job backlog was draining.

---

## CHG-001 — Earlier corrections in this session

Recorded in full in the session transcript; summarised here for traceability.

| Change | Files | Evidence |
|---|---|---|
| `Repair Solution` re-keyed on `solution_code`; one label can now serve several Issue Categories | `repair_solution.json/.py`, patches `v04`, `v05`, `catalogue_sync.py` | 36/36 renamed, 0 dangling across 2,271 link rows, `GFR-*` Items byte-identical |
| Ops Hub quick-create is category-aware and honest (`selected` / `exists_elsewhere` / `reused` / `reactivated`) | `gofix_ops_hub.py/.js` | `test_fix1` 25/25 |
| Solution applicability (`GoFix Solution Applicability`) filters the picker by device | new child DocType, `api.py`, patch `v06` | `test_fix3` 35/35 |
| `Swapping Board` (`BRD-SWP`) added; 14 spares re-pointed off Board-Level Repair | seeder, `v01`, `v02`, patch `v07` | priced ₹1,500 vs ₹2,000; regression 30/30 |
| One device-condition vocabulary, published on boot | `constants/device_condition.py`, `boot.py`, `service_request.json`, both POS workspaces | `test_intake` 27/27 |
| Walk-in conversion resolves customer + issue server-side | `ch_pos/api/token_api.py`, `queue_workspace.js` | `test_intake` |
| Walk-in phone → existing-customer lookup; token linked at intake | `token_api.py`, `sidebar.js` | `test_walkin_lookup` 22/22 |
| Rejections visible in Ops Hub + `GoFix Rejection Register` report | `gofix_ops_hub.py/.js`, new report | `test_rejection` 26/26 |
| Purchase destination cannot be a Customer Device bin; "Target Store" disambiguated | `customer_device_stock.py`, `hooks.py`, patch `v08`, `ch_erp15/custom/purchase_order.json` | `test_rejection` §4 |
| POS logout POSTs instead of navigating (403 fix) | `session_controls.js` | GET→403 / POST→200 proven on the running server |
