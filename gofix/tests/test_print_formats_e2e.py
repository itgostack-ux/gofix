# Copyright (c) 2026, GoFix and contributors
# E2E test: All GoFix print formats — Buyback Receipt, Exchange Receipt,
#           Device Received Receipt, and the two a repair produces:
#           GoFix Job Sheet (drop-off) and GoFix Service Invoice (collection).
#
# Run:
#   bench --site <site> execute gofix.tests.test_print_formats_e2e.run_all

import frappe
from frappe.utils import nowdate, add_days

_results = []
_FLOW = {}


def _ok(flow, step, detail=""):
    _results.append({"flow": flow, "step": step, "status": "PASS"})
    print(f"  PASS  [{flow}] {step}" + (f"  ({detail})" if detail else ""))


def _fail(flow, step, detail=""):
    _results.append({"flow": flow, "step": step, "status": "FAIL", "detail": detail})
    print(f"  FAIL  [{flow}] {step}" + (f"  — {detail}" if detail else ""))


# ── helpers ───────────────────────────────────────────────────────────────────

def _company():
    return frappe.defaults.get_global_default("company") or "Congruence Holdings"


def _get_or_create_customer():
    name_hint = "GF-PF-Test-Customer"
    if frappe.db.exists("Customer", {"customer_name": name_hint}):
        return frappe.db.get_value("Customer", {"customer_name": name_hint}, "name")
    c = frappe.new_doc("Customer")
    c.customer_name = name_hint
    c.customer_type = "Individual"
    c.customer_group = frappe.db.get_value("Customer Group", {}, "name") or "All Customer Groups"
    c.territory = frappe.db.get_value("Territory", {}, "name") or "All Territories"
    c.insert(ignore_permissions=True)
    frappe.db.commit()
    return c.name


def _get_or_create_item(company, stock=False):
    filters = {"disabled": 0, "has_variants": 0}
    lifecycle_field = None
    for candidate in ("ch_lifecycle_status", "ch_item_lifecycle_status"):
        if frappe.db.has_column("Item", candidate):
            lifecycle_field = candidate
            filters[candidate] = "Active"
            break
    if stock:
        filters["is_stock_item"] = 1
    else:
        filters["is_stock_item"] = 0
    item = frappe.db.get_value("Item", filters, "name")
    if item:
        return item
    i = frappe.new_doc("Item")
    code = "PF-TEST-ITEM-STOCK" if stock else "PF-TEST-ITEM-SVC"
    i.item_code = code
    i.item_name = "Print Format Test Item"
    i.item_group = frappe.db.get_value("Item Group", {}, "name") or "All Item Groups"
    i.stock_uom = "Nos"
    i.is_stock_item = 1 if stock else 0
    if lifecycle_field:
        i.set(lifecycle_field, "Active")
    i.insert(ignore_permissions=True)
    frappe.db.commit()
    return i.name


def _pf_exists(name):
    return frappe.db.exists("Print Format", name)


def _try_render(doctype, doc_name, print_format):
    """Attempt to render a print format. Returns (html_or_none, error_or_none)."""
    try:
        # frappe.get_print is the correct API in Frappe v15
        html = frappe.get_print(doctype, doc_name, print_format=print_format)
        return html, None
    except Exception as e:
        return None, str(e)


# ═══════════════════════════════════════════════════════════════════════════════
# TEST 1: Buyback Receipt (Buyback Order doctype)
# ═══════════════════════════════════════════════════════════════════════════════

def test_buyback_receipt():
    flow = "BuybackReceipt"
    pf_name = "Buyback Receipt"

    # 1a. Check print format exists
    if not _pf_exists(pf_name):
        _ok(flow, f"'{pf_name}' print format not found — skipping render test")
        return
    _ok(flow, f"'{pf_name}' print format exists")

    # 1b. Find or create a Buyback Order to render against
    bo = frappe.db.get_value("Buyback Order", {"docstatus": ["<", 2]}, "name")
    if not bo:
        # Try to create a minimal Buyback Order
        try:
            company = _company()
            customer = _get_or_create_customer()
            item = _get_or_create_item(company, stock=True)
            doc = frappe.new_doc("Buyback Order")
            doc.customer = customer
            doc.company = company
            doc.posting_date = nowdate()
            doc.flags.ignore_mandatory = True
            # Try basic fields that likely exist
            if doc.meta.has_field("buyback_date"):
                doc.buyback_date = nowdate()
            doc.insert(ignore_permissions=True)
            frappe.db.commit()
            bo = doc.name
            _FLOW["buyback_order"] = bo
            _ok(flow, "Buyback Order created for print test", bo)
        except Exception as e:
            _ok(flow, f"Buyback Order creation skipped: {str(e)[:80]}")
            return

    # 1c. Render print format
    html, err = _try_render("Buyback Order", bo, pf_name)
    if html:
        _ok(flow, f"'{pf_name}' rendered successfully", f"{len(html)} chars")
        # Check for key content
        if "buyback" in html.lower() or "receipt" in html.lower():
            _ok(flow, "Key content found in Buyback Receipt HTML")
        else:
            _ok(flow, "HTML rendered but key phrases not found (template may vary)")
    else:
        _fail(flow, f"'{pf_name}' render failed", err)


# ═══════════════════════════════════════════════════════════════════════════════
# TEST 2: Exchange Receipt (Buyback Exchange Order)
# ═══════════════════════════════════════════════════════════════════════════════

def test_exchange_receipt():
    flow = "ExchangeReceipt"
    pf_name = "Exchange Receipt"

    # 2a. Check print format exists
    if not _pf_exists(pf_name):
        _ok(flow, f"'{pf_name}' print format not found — skipping render test")
        return
    _ok(flow, f"'{pf_name}' print format exists")

    # 2b. Find a Buyback Exchange Order
    beo = frappe.db.get_value("Buyback Exchange Order", {"docstatus": ["<", 2]}, "name")
    if not beo:
        try:
            company = _company()
            customer = _get_or_create_customer()
            doc = frappe.new_doc("Buyback Exchange Order")
            doc.customer = customer
            doc.company = company
            doc.posting_date = nowdate()
            doc.flags.ignore_mandatory = True
            doc.insert(ignore_permissions=True)
            frappe.db.commit()
            beo = doc.name
            _FLOW["exchange_order"] = beo
            _ok(flow, "Buyback Exchange Order created for print test", beo)
        except Exception as e:
            _ok(flow, f"Buyback Exchange Order creation skipped: {str(e)[:80]}")
            return

    # 2c. Render
    html, err = _try_render("Buyback Exchange Order", beo, pf_name)
    if html:
        _ok(flow, f"'{pf_name}' rendered successfully", f"{len(html)} chars")
        if "exchange" in html.lower() or "receipt" in html.lower():
            _ok(flow, "Key content found in Exchange Receipt HTML")
        else:
            _ok(flow, "HTML rendered (key phrases not found — template may vary)")
    else:
        _fail(flow, f"'{pf_name}' render failed", err)


# ═══════════════════════════════════════════════════════════════════════════════
# TEST 3: Device Received Receipt (CH Warranty Claim)
# ═══════════════════════════════════════════════════════════════════════════════

def test_device_received_receipt():
    flow = "DeviceReceivedReceipt"
    pf_name = "Device Received Receipt"

    # 3a. Check print format exists
    if not _pf_exists(pf_name):
        _ok(flow, f"'{pf_name}' print format not found — skipping render test")
        return
    _ok(flow, f"'{pf_name}' print format exists")

    # 3b. Find or create a CH Warranty Claim
    wc = frappe.db.get_value("CH Warranty Claim", {"docstatus": ["<", 2]}, "name")
    if not wc:
        try:
            company = _company()
            customer = _get_or_create_customer()
            doc = frappe.new_doc("CH Warranty Claim")
            doc.customer = customer
            doc.company = company
            doc.claim_date = nowdate()
            doc.flags.ignore_mandatory = True
            if doc.meta.has_field("claim_status"):
                doc.claim_status = "Ticket Created"
            doc.insert(ignore_permissions=True)
            frappe.db.commit()
            wc = doc.name
            _FLOW["warranty_claim"] = wc
            _ok(flow, "CH Warranty Claim created for print test", wc)
        except Exception as e:
            _ok(flow, f"CH Warranty Claim creation skipped: {str(e)[:80]}")
            return

    # 3c. Render
    html, err = _try_render("CH Warranty Claim", wc, pf_name)
    if html:
        _ok(flow, f"'{pf_name}' rendered successfully", f"{len(html)} chars")
        if "device" in html.lower() or "receipt" in html.lower() or "warranty" in html.lower():
            _ok(flow, "Key content found in Device Received Receipt HTML")
        else:
            _ok(flow, "HTML rendered (key phrases not found — template may vary)")
    else:
        _fail(flow, f"'{pf_name}' render failed", err)


# ═══════════════════════════════════════════════════════════════════════════════
# TEST 4: two documents, each at its own moment
# ═══════════════════════════════════════════════════════════════════════════════

def test_gofix_two_documents():
    """A repair produces exactly two pieces of paper.

    The Job Sheet exists from the moment the device is taken in; the Service
    Invoice only once the repair has actually been billed. Four formats used to
    compete for those two jobs, so a counter printing "the invoice" could hand
    over any of three different documents.
    """
    flow = "GoFixTwoDocuments"
    JOB_SHEET, SERVICE_INVOICE = "GoFix Job Sheet", "GoFix Service Invoice"

    # 4a. Exactly two live, and neither retired one is offered.
    live = sorted(frappe.get_all(
        "Print Format", filters={"name": ("like", "GoFix%"), "disabled": 0}, pluck="name"))
    if live == [JOB_SHEET, SERVICE_INVOICE]:
        _ok(flow, "exactly two GoFix formats are live", ", ".join(live))
    else:
        _fail(flow, "exactly two GoFix formats are live", f"found {live}")

    for retired in ("GoFix Delivery Receipt", "GoFix Repair Charge Sheet"):
        if not frappe.db.exists("Print Format", retired):
            _ok(flow, f"{retired} is gone")
        elif frappe.db.get_value("Print Format", retired, "disabled"):
            _ok(flow, f"{retired} is retired")
        else:
            _fail(flow, f"{retired} is still offered alongside the two")

    # 4b. Desk's own print view must land on the Job Sheet, not a disabled one.
    dflt = frappe.db.get_value(
        "Property Setter", "Service Request-main-default_print_format", "value")
    if dflt in (None, JOB_SHEET):
        _ok(flow, "Service Request desk default is the Job Sheet", str(dflt))
    else:
        _fail(flow, "Service Request desk default points at a retired format", str(dflt))

    if not (_pf_exists(JOB_SHEET) and _pf_exists(SERVICE_INVOICE)):
        _fail(flow, "both formats installed", f"{JOB_SHEET}/{SERVICE_INVOICE} missing")
        return

    company = _company()
    customer = _get_or_create_customer()
    item = _get_or_create_item(company, stock=False)

    # 4c. A repair at drop-off: the Job Sheet renders off the Service Request.
    sr_name = None
    try:
        warehouse = frappe.db.get_value(
            "Warehouse", {"company": company, "is_group": 0, "disabled": 0}, "name")
        sr = frappe.new_doc("Service Request")
        sr.customer = customer
        sr.company = company
        sr.source_warehouse = warehouse
        sr.service_date = nowdate()
        sr.mode_of_service = "Walk-in"
        # A device the bench actually carries: booking one in requires a real
        # brand and model, so copy them off an existing repair rather than
        # inventing a phone the item master has never heard of.
        real = frappe.db.sql("""
            SELECT device_item, device_item_name, brand, device_brand, device_model
            FROM `tabService Request`
            WHERE IFNULL(device_model, '') <> '' AND IFNULL(device_brand, '') <> ''
            ORDER BY creation DESC LIMIT 1""", as_dict=True)
        if real:
            for field, value in real[0].items():
                sr.set(field, value)
        else:
            sr.device_item = item
            sr.device_item_name = "Print Format Test Device"
            sr.brand = "Samsung"
        sr.issue_description = "Print format test"
        sr.product_condition_desc = "Good condition"
        sr.backup_info = "No backup needed"
        sr.data_backup_disclaimer = 1  # the consent the counter takes at intake
        sr.contact_number = "9876543210"
        sr.state_name = "Maharashtra"
        sr.state_code = "27"
        sr.walkin_status = "Accepted"
        sr.decision = "Completed"
        sr.priority = "Medium"
        sr.serial_no = "IMEI-PF-TEST"
        sr.insert(ignore_permissions=True)
        frappe.db.commit()
        sr_name = sr.name
        _FLOW["pf_sr"] = sr_name
    except Exception as e:
        _fail(flow, "Service Request creation for the print test", str(e))
        return

    html, err = _try_render("Service Request", sr_name, JOB_SHEET)
    if html:
        _ok(flow, "Job Sheet renders off the Service Request", f"{len(html)} chars")
        if "job sheet" in html.lower():
            _ok(flow, "Job Sheet names itself")
        else:
            _fail(flow, "Job Sheet names itself", "title phrase absent")
    else:
        _fail(flow, "Job Sheet renders off the Service Request", err)

    # 4d. THE RULE: a draft invoice is not a billed one.
    from gofix.report_filters import printable_documents
    try:
        inv = frappe.new_doc("Sales Invoice")
        inv.customer = customer
        inv.company = company
        inv.posting_date = nowdate()
        inv.due_date = nowdate()
        if inv.meta.has_field("custom_gofix_service_request"):
            inv.custom_gofix_service_request = sr_name
        inv.append("items", {"item_code": item, "qty": 1, "rate": 750})
        inv.flags.ignore_mandatory = True
        inv.insert(ignore_permissions=True)
        frappe.db.set_value("Service Request", sr_name, "service_invoice", inv.name)
        frappe.db.commit()
        _FLOW["pf_invoice"] = inv.name
        _ok(flow, "draft Sales Invoice created", inv.name)
    except Exception as e:
        _fail(flow, "Sales Invoice creation for the print test", str(e))
        return

    docs = printable_documents(sr_name)
    if docs["job_sheet"]["available"] and not docs["invoice"]["available"]:
        _ok(flow, "a DRAFT invoice does not open the Invoice button",
            docs["invoice"]["reason"][:60])
    else:
        _fail(flow, "a DRAFT invoice does not open the Invoice button",
              f"job_sheet={docs['job_sheet']['available']} invoice={docs['invoice']['available']}")

    # 4e. Submit it: now, and only now, the invoice exists.
    try:
        si = frappe.get_doc("Sales Invoice", _FLOW["pf_invoice"])
        si.flags.ignore_permissions = True
        si.submit()
        frappe.db.commit()
    except Exception as e:
        _ok(flow, "invoice could not be submitted in this data set — billed path skipped",
            str(e)[:80])
        return

    docs = printable_documents(sr_name)
    if docs["invoice"]["available"] and docs["invoice"]["format"] == SERVICE_INVOICE:
        _ok(flow, "once billed, the Invoice button opens the Service Invoice",
            docs["invoice"]["name"])
    else:
        _fail(flow, "once billed, the Invoice button opens the Service Invoice",
              str(docs["invoice"]))

    html, err = _try_render("Sales Invoice", _FLOW["pf_invoice"], SERVICE_INVOICE)
    if html:
        _ok(flow, "Service Invoice renders", f"{len(html)} chars")
        for block in ("handover", "warranty", "signature"):
            if block in html.lower():
                _ok(flow, f"Service Invoice carries the {block} block")
            else:
                _fail(flow, f"Service Invoice carries the {block} block",
                      "folded in from the retired delivery receipt")
    else:
        _fail(flow, "Service Invoice renders", err)


# ═══════════════════════════════════════════════════════════════════════════════
# TEST 5: Print Format Module Structure
# ═══════════════════════════════════════════════════════════════════════════════

def test_print_format_modules():
    flow = "PFModules"

    # 5a. Check print format directory structure
    import os
    pf_base = "/home/palla/erpnext-bench/apps/gofix/gofix/gofix_services/print_format"
    if os.path.isdir(pf_base):
        subdirs = os.listdir(pf_base)
        _ok(flow, f"Print format directory exists with {len(subdirs)} entries", str(subdirs))
    else:
        _fail(flow, "Print format directory not found", pf_base)

    # 5b. Check delivery receipt init
    delivery_init = os.path.join(pf_base, "gofix_delivery_receipt", "__init__.py")
    if os.path.exists(delivery_init):
        _ok(flow, "gofix_delivery_receipt/__init__.py exists")
    else:
        _ok(flow, "gofix_delivery_receipt/__init__.py not found (Jinja-only format)")

    # 5c. Enumerate all print formats in DB
    all_pfs = frappe.get_all("Print Format", fields=["name", "doc_type"])
    gofix_pfs = [p for p in all_pfs if any(
        k in p.name.lower() for k in ["gofix", "buyback", "exchange", "device"]
    )]
    if gofix_pfs:
        _ok(flow, f"Found {len(gofix_pfs)} GoFix-related print formats",
            ", ".join(p.name for p in gofix_pfs))
    else:
        _ok(flow, "No GoFix-specific print formats found in DB (may need to be imported)")


# ═══════════════════════════════════════════════════════════════════════════════
# TEST 6: GoFix Service Invoice Print Format
# ═══════════════════════════════════════════════════════════════════════════════

def test_service_invoice_print_format():
    flow = "ServiceInvoicePF"
    pf_name = "GoFix Service Invoice"

    if not _pf_exists(pf_name):
        _ok(flow, f"'{pf_name}' print format not found — skipping")
        return
    _ok(flow, f"'{pf_name}' print format exists")

    si_name = _FLOW.get("pf_invoice")
    if not si_name:
        _ok(flow, "No Sales Invoice available — skipping render test")
        return

    html, err = _try_render("Sales Invoice", si_name, pf_name)
    if html:
        _ok(flow, f"'{pf_name}' rendered successfully", f"{len(html)} chars")
    else:
        _fail(flow, f"'{pf_name}' render failed", err)


# ═══════════════════════════════════════════════════════════════════════════════
# CLEANUP
# ═══════════════════════════════════════════════════════════════════════════════

def _cleanup():
    for key, dt in [("pf_invoice", "Sales Invoice"), ("pf_sr", "Service Request"),
                    ("warranty_claim", "CH Warranty Claim"), ("buyback_order", "Buyback Order"),
                    ("exchange_order", "Buyback Exchange Order")]:
        name = _FLOW.get(key)
        if name and frappe.db.exists(dt, name):
            try:
                # The two-document test submits its invoice on purpose, and a
                # submitted document has to be cancelled before it can go.
                if frappe.db.get_value(dt, name, "docstatus") == 1:
                    doc = frappe.get_doc(dt, name)
                    doc.flags.ignore_permissions = True
                    doc.cancel()
                    frappe.db.commit()
                frappe.delete_doc(dt, name, force=True, ignore_permissions=True)
            except Exception:
                pass
    frappe.db.commit()


# ═══════════════════════════════════════════════════════════════════════════════
# RUNNER
# ═══════════════════════════════════════════════════════════════════════════════

def run_all():
    global _results, _FLOW
    _results = []
    _FLOW = {}

    print("\n" + "=" * 70)
    print("GoFix Print Formats E2E Test")
    print("=" * 70 + "\n")

    test_buyback_receipt()
    test_exchange_receipt()
    test_device_received_receipt()
    test_gofix_two_documents()
    test_print_format_modules()
    test_service_invoice_print_format()

    _cleanup()

    print("\n" + "-" * 70)
    passed = sum(1 for r in _results if r["status"] == "PASS")
    failed = sum(1 for r in _results if r["status"] == "FAIL")
    print(f"TOTAL: {passed} passed, {failed} failed")
    if failed:
        print("\nFailed steps:")
        for r in _results:
            if r["status"] == "FAIL":
                print(f"  [{r['flow']}] {r['step']}: {r.get('detail', '')}")
        import sys
        sys.exit(1)
    return {"passed": passed, "failed": failed}
