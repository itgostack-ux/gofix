# 01 — Application Architecture (Phase 1: Discovery)

**Status:** Phase 1 in progress
**Audited commit:** `gofix@5ea35f9`, `ch_pos@c478f78`, `ch_erp15@033eefa`
**Site:** `erpnext.local` (db `_7e1ff8b754d64fee`), MariaDB, Frappe/ERPNext v15-line, Python 3.14
**Method:** every figure below was produced by a command against the working tree or the live DB. Nothing here is inferred.

> Discovery only. No structural changes were made while producing this document.

---

## 1.1 Module inventory (measured)

| Area | Measure |
|---|---|
| Python files (`gofix`) | 254 |
| JS files (`gofix`) | 27 |
| Python LOC — `gofix_services` | 19,433 |
| Python LOC — `setup` | 4,113 |
| Python LOC — `patches` | 3,396 |
| Python LOC — `api` | 1,413 |
| Python LOC — `www` | 102 |
| Declared Frappe modules | `GoFix`, `GoFix Services` (`gofix/modules.txt`) |
| DocTypes owned | 40 (24 masters + 16 child tables) |
| Whitelisted endpoints | 177 |
| Test files | 23 |

`gofix/doctype/` and `gofix/templates/` are 0 LOC — the `GoFix` module is effectively an empty
shell; all real doctypes live under `GoFix Services`. **Finding candidate (low):** a declared
module with no content.

---

## 1.2 DocType inventory

**Masters (24)** — `GoFix Approval Rule`, `GoFix Brand Option`, `GoFix Cancellation Reason`,
`GoFix Custody Log`, `GoFix Device Type`, `GoFix Pricing Rule`, `GoFix QC Template`,
`GoFix Repair Cost Template`, `GoFix SLA Rule`, `GoFix Settings` (Single),
`GoFix Symptom`, `GoFix Token`, `GoFix Visit Reason`, `Issue Category`, `Job Assignment`,
`Repair Solution`, `Service Order State`, `Service Order Transition`, `Service Request`,
`Solution Spare Mapping`, `Spare Parts Usage`, `Technician Grade`, `Walkin Source`,
`Withdrawal Reason`.

**Child tables (16)** — `Estimate Version`, `GoFix Item Repair Solution`, `GoFix QC Checklist`,
`GoFix QC Template Item`, `GoFix Repair Cost Template Item`, `GoFix Solution Applicability`,
`GoFix Spare Compatible Model`, `GoFix Status Log`, `GoFix Token Issue`,
`GoFix Token Status Log`, `SR Issue Line`, `SR Solution Line`, `SR Spare Line`,
`Service Request Service Item`, `Technician Audit`, `Technician Skill`.

### `Service Request` — the aggregate root

- **172 fields**, `is_submittable: 1`, `autoname: format:SR-{YY}{MM}{DD}-{####}`
- Child tables: `issue_lines` (SR Issue Line), `solution_lines` (SR Solution Line),
  `spare_lines` (SR Spare Line), `service_items` (Service Request Service Item),
  `status_log` (GoFix Status Log)
- `decision` (Select) is the lifecycle field:
  `Draft, Accepted, In Service, Completed, Invoiced, Delivered, Withdrawn, Rejected, Expired, Cancelled`

**Finding candidate (high) — §11 scope.** The only completion-target field is
`expected_completion_date`, typed **`Date`**, not `Datetime`
(`gofix_services/doctype/service_request/service_request.json`). The brief requires an
authoritative estimated-completion **date-and-time** plus a live countdown. A `Date` field
cannot express a time-of-day target, so the countdown requirement is unimplementable against
the current schema without a field change + migration. Recorded for Phase 2/3.

A 172-field submittable doctype is itself a structural risk (§4): it strongly suggests
denormalised copies of Customer / Item / Warehouse / Invoice data. To be proven field-by-field
in `05-form-field-mapping.md`, not asserted here.

---

## 1.3 Integration map — how a ticket is born

Production code paths that create a `Service Request` (grep across `gofix`, `ch_pos`,
`buyback`, `ch_logistics`, excluding tests/scripts):

| # | Entry point | Symbol | Creates |
|---|---|---|---|
| 1 | POS counter intake | `ch_pos.api.repair.create_service_intake_from_pos` (repair.py:47) and `create_repair_intake` (repair.py:128) | SR direct, `decision="Draft"`, then auto-calls `open_walkin_job` |
| 2 | POS queue token conversion | `ch_pos.api.token_api.convert_token_to_gofix` (token_api.py ~2035) | SR from a `POS Kiosk Token` |
| 3 | Repeat / reopened repair | `gofix.gofix_services.api.reopen_repair` (api.py:2058) | a new SR from a prior `SR Solution Line` |

**Only three.** Against the brief's nine required intake channels (§2) this is the current
reality:

| Required channel | Present? | Evidence |
|---|---|---|
| Walk-in customer | Yes | path 1 + `Walkin Source` doctype |
| Direct / manual ticket | Yes | Desk form on `Service Request` |
| Website booking | **No SR path** | `gofix/www/` has only `gofix-token`, `gofix-mgmt`, `track-repair` (read/track only); no `website_route_rules` in `hooks.py` |
| Mobile application | Not found | no mobile entry point in this app |
| Call-centre / CRM | **No dedicated path** | would use the Desk form |
| Pickup / doorstep | Partial | SR has `pickup_scheduled_datetime` / `pickup_completed_datetime` fields, but no distinct intake path |
| Imported / API-created | Via the 3 whitelisted paths only | no bulk/import intake found |
| Repeat / reopened | Yes | path 3 |
| Warranty / return repair | Partial | `warranty_status`, `repair_warranty_expiry` fields exist; flow to be traced in Phase 2 |

**Finding candidate (medium/high):** the customer-facing web page `track-repair` is
read-only tracking; there is no self-service booking that produces a Service Request. To be
confirmed against product intent before being called a defect (**decision required**).

---

## 1.4 Hooks surface (`gofix/hooks.py`)

| Hook | Declared |
|---|---|
| `doc_events` | yes |
| `scheduler_events` | yes |
| `permission_query_conditions` | yes |
| `has_permission` | yes |
| `override_doctype_class` | yes (`Sales Order`) |
| `fixtures` | yes |
| `boot_session` | yes |
| `after_migrate` | yes |
| `app_include_js` | yes |
| `jinja` | yes |
| `override_whitelisted_methods` | — |
| `before_migrate` | — |
| `website_route_rules` | — |

**Scheduled jobs (4):**
- `service_request.flag_unclaimed_devices`
- `service_request.auto_expire_stale_requests`
- `gofix_services.api.expire_pending_estimates`
- `gofix_sla_rule.check_gofix_sla_breach`

---

## 1.5 Configuration surface

`GoFix Settings` is a **Single with 84 fields** and is a genuinely mature config surface —
role lists, row limits, rate limits, OTP TTLs, company/currency/country, tax template source,
default parts warehouse. This materially changes the §1 hardcoding audit: the mechanism
already exists, so the question is **whether code reads it** rather than whether it exists.

Observed defaults that are business-specific and shipped in the DocType JSON (to be classified
in `03-hardcoding-audit.md`, not pre-judged here):
`company_abbreviation='GF'`, `company_currency='INR'`, `company_country='India'`,
and role-name string defaults such as `'Service Manager'`, `'QC Manager'`, `'Sales Manager'`.

---

## 1.6 Existing automated-test inventory — **BLOCKER**

| Measure | Value |
|---|---|
| Test files | 23 |
| Modules under `gofix/tests` | 19 |
| Discoverable `unittest` cases | **82** |
| Modules contributing **0** cases | **8 of 19** |

`bench --site erpnext.local run-tests --app gofix --module gofix.tests.test_workflow`
starts, logs type-validator setup, and reports **no results at all** — no pass/fail counts
(`logs/frappe.testing.log`).

The 8 modules that yield zero cases are precisely the end-to-end ones this audit depends on:

`test_workflow`, `test_service_workflow`, `test_warranty_full_cycle_e2e`, `test_sla_breach_e2e`,
`test_spare_model_compatibility`, `test_technician_intelligence_e2e`, `test_print_formats_e2e`,
`test_replacement_gap_e2e`.

**Root cause (confirmed by reading the files):** they are *script-style* tests — plain module
functions with a `run_all()` entrypoint, invoked as
`bench execute gofix.tests.<mod>.run_all` — not `unittest.TestCase` subclasses. No standard
runner will ever execute them.

**Impact:** a CI run of `bench run-tests` reports success while never executing the warranty
lifecycle, SLA breach, spare compatibility or print-format suites. Any claim that "the E2E
tests pass" is unfounded unless each `run_all` is invoked by hand.

Recorded as **FINDING-TEST-001 (High)** for Phase 2.

---

## 1.7 Environment & access blockers

| # | Blocker | Evidence | Effect on this audit |
|---|---|---|---|
| B1 | **A second Claude Code session is writing to this same bench.** | `ch_pos` HEAD moved to `c478f78` and `ch_erp15` to `033eefa` mid-session; commit `5ea35f9` (my in-progress gofix work) was committed by that session, not by me | The tree moves under a line-by-line audit. Findings can go stale between phases. **Needs a decision from the user.** |
| B2 | Standard test runner returns no results | §1.6 | Regression evidence must come from hand-invoked `run_all` + the bespoke harness built earlier this session |
| B3 | `bench migrate` intermittently aborts on `DocumentLockedError` | `Role Profile.on_update` → `queue_action` writes a lock file *before* an `enqueue_after_commit`; an aborted migrate orphans locks | Schema/patch work must be sequenced around a drained job queue |
| B4 | `custom/<doctype>.json` fixtures with `sync_on_migrate` silently override app-declared field properties | Two instances already proven: `ch_erp15/custom/purchase_order.json` (label) and `ch_erp15/custom/item.json` (`ch_model reqd=1` overriding `ch_item_master`'s `reqd=0`) | Any schema remediation must check these files or it will be silently reverted on the next migrate |
| B5 | No Administrator password in `site_config.json` | verified | Authenticated HTTP testing uses `bench browse --user Administrator` to mint a session |
| B6 | Uncommitted work in 3 apps (gofix 14, ch_pos 5, ch_erp15 3 files) | `git status` | Audit baseline is not a clean tree |
| B7 | Internet access for §13 benchmarking not yet verified | — | Competitor claims will be labelled *unverified* unless fetched from official docs |

---

## 1.8 What this document does **not** yet establish

Deliberately out of scope for Phase 1, to avoid asserting unproven conclusions:

- whether each of the 172 `Service Request` fields is actually persisted and re-read (§5)
- whether the 177 endpoints enforce server-side permission (§12)
- whether stock/accounting reconcile (§8, §9)
- whether damaged spares can reach an invoice (§9)
- the real status-transition graph vs the declared `Service Order State` / `Service Order Transition` masters (§7)

Each is a Phase 2 work item with its own deliverable.
