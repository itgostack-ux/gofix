# Copyright (c) 2025, GoFix and contributors
# For license information, please see license.txt

"""
Create GOFIX SOLUTIONS PRIVATE LIMITED as the GoFix operating company.
Idempotent — safe to run multiple times.

Usage:
    bench --site erpnext.local execute gofix.setup.setup_gofix_company.execute
"""

import frappe
from frappe.utils import now_datetime

COMPANY_NAME = "GOFIX SOLUTIONS PRIVATE LIMITED"
COMPANY_ABBR = "GSPL"
GSTIN = "33AAJCG6064A1ZY"
CURRENCY = "INR"
COUNTRY = "India"


def _ok(msg):
    print(f"  ✅ {msg}")


def _skip(msg):
    print(f"  ⏭  {msg}")


def _warn(msg):
    print(f"  ⚠️  {msg}")


# ──────────────────────────────────────────────────────────────
# 1. Company
# ──────────────────────────────────────────────────────────────

def create_company():
    if frappe.db.exists("Company", COMPANY_NAME):
        _skip(f"Company '{COMPANY_NAME}' already exists")
        return frappe.get_doc("Company", COMPANY_NAME)

    company = frappe.get_doc({
        "doctype": "Company",
        "company_name": COMPANY_NAME,
        "abbr": COMPANY_ABBR,
        "default_currency": CURRENCY,
        "country": COUNTRY,
        "gstin": GSTIN,
        "domain": "Services",
    })
    company.insert(ignore_permissions=True)
    frappe.db.commit()
    _ok(f"Company created: {COMPANY_NAME} ({COMPANY_ABBR}), GSTIN={GSTIN}")
    return company


# ──────────────────────────────────────────────────────────────
# 2. Warehouses
# ──────────────────────────────────────────────────────────────

_WAREHOUSES = [
    # (warehouse_name, warehouse_type, parent_name_suffix, is_group)
    ("GoFix Stores", "Stores", None, False),
    ("Work In Progress", "Work In Progress", None, False),
    ("GoFix Repair Hub", "Stores", None, False),       # master hub warehouse
    ("Supplier Return", "Stores", None, False),        # supplier return warehouse
    ("Damaged Stock", "Stores", None, False),          # damaged stock warehouse
    ("Finished Goods", "Stores", None, False),
]


def create_warehouses():
    created = []
    for wh_name, wh_type, _, _ in _WAREHOUSES:
        full_name = f"{wh_name} - {COMPANY_ABBR}"
        if frappe.db.exists("Warehouse", full_name):
            _skip(f"Warehouse '{full_name}' already exists")
            continue
        wh = frappe.get_doc({
            "doctype": "Warehouse",
            "warehouse_name": wh_name,
            "warehouse_type": wh_type if frappe.db.exists("Warehouse Type", wh_type) else None,
            "company": COMPANY_NAME,
            "is_group": 0,
        })
        wh.insert(ignore_permissions=True)
        created.append(full_name)
        _ok(f"Warehouse: {full_name}")

    if created:
        frappe.db.commit()
    return created


# ──────────────────────────────────────────────────────────────
# 3. Set GoFix custom fields on the company
# ──────────────────────────────────────────────────────────────

def set_company_gofix_fields():
    """Set master_hub_warehouse, supplier_return_warehouse, damaged_stock_warehouse
    on the company — these custom fields are created by gofix.setup.install."""

    meta = frappe.get_meta("Company")
    custom_field_map = {
        "master_hub_warehouse": f"GoFix Repair Hub - {COMPANY_ABBR}",
        "supplier_return_warehouse": f"Supplier Return - {COMPANY_ABBR}",
        "damaged_stock_warehouse": f"Damaged Stock - {COMPANY_ABBR}",
    }

    updates = {}
    for fieldname, warehouse_name in custom_field_map.items():
        if not meta.get_field(fieldname):
            _warn(f"Custom field Company.{fieldname} not found — run gofix install first")
            continue
        if not frappe.db.exists("Warehouse", warehouse_name):
            _warn(f"Warehouse '{warehouse_name}' not found — skipping field {fieldname}")
            continue
        updates[fieldname] = warehouse_name

    if updates:
        frappe.db.set_value("Company", COMPANY_NAME, updates)
        frappe.db.commit()
        for k, v in updates.items():
            _ok(f"Company.{k} = {v}")


# ──────────────────────────────────────────────────────────────
# 4. Default accounts (set defaults ERPNext may have missed)
# ──────────────────────────────────────────────────────────────

def set_company_defaults():
    """Set default_warehouse and cost_center on the company if not already set."""
    company_doc = frappe.get_doc("Company", COMPANY_NAME)

    updates = {}
    stores_wh = f"GoFix Stores - {COMPANY_ABBR}"
    if not company_doc.default_fg_warehouse and frappe.db.exists("Warehouse", stores_wh):
        updates["default_fg_warehouse"] = stores_wh

    default_cc = f"Main - {COMPANY_ABBR}"
    if not company_doc.cost_center and frappe.db.exists("Cost Center", default_cc):
        updates["cost_center"] = default_cc

    if updates:
        frappe.db.set_value("Company", COMPANY_NAME, updates)
        frappe.db.commit()
        for k, v in updates.items():
            _ok(f"Company default {k} = {v}")


# ──────────────────────────────────────────────────────────────
# 5. GST Tax Templates (In-State + Out-State)
# ──────────────────────────────────────────────────────────────

def create_gst_tax_templates():
    """Clone standard GST tax templates from BestBuy for the GoFix company."""

    templates = [
        ("Output GST In-state", "Sales", [
            ("CGST - GSPL", "output", 9),
            ("SGST - GSPL", "output", 9),
        ]),
        ("Output GST Out-state", "Sales", [
            ("IGST - GSPL", "output", 18),
        ]),
        ("Input GST In-state", "Purchase", [
            ("Input CGST - GSPL", "input", 9),
            ("Input SGST - GSPL", "input", 9),
        ]),
        ("Input GST Out-state", "Purchase", [
            ("Input IGST - GSPL", "input", 18),
        ]),
    ]

    created = 0
    for template_name_base, template_type, _ in templates:
        full_name = f"{template_name_base} - {COMPANY_ABBR}"
        if frappe.db.exists("Sales Taxes and Charges Template" if template_type == "Sales" else "Purchase Taxes and Charges Template", full_name):
            _skip(f"Tax Template '{full_name}' already exists")
            continue

        # Look up corresponding BestBuy template to clone from
        bmpl_name = f"{template_name_base} - BMPL"
        source_doctype = "Sales Taxes and Charges Template" if template_type == "Sales" else "Purchase Taxes and Charges Template"
        if not frappe.db.exists(source_doctype, bmpl_name):
            _warn(f"Source template '{bmpl_name}' not found — skipping '{full_name}'")
            continue

        source = frappe.get_doc(source_doctype, bmpl_name)
        new_tmpl = frappe.copy_doc(source)
        new_tmpl.title = full_name
        new_tmpl.company = COMPANY_NAME
        new_tmpl.is_default = 0
        new_tmpl.name = full_name
        # Update account heads in rows to GSPL equivalents where possible
        for row in new_tmpl.taxes:
            if row.account_head:
                gspl_account = row.account_head.replace("- BMPL", f"- {COMPANY_ABBR}")
                if frappe.db.exists("Account", gspl_account):
                    row.account_head = gspl_account
        new_tmpl.insert(ignore_permissions=True)
        created += 1
        _ok(f"Tax Template: {full_name}")

    if created:
        frappe.db.commit()


# ──────────────────────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────────────────────

def execute():
    print("\n" + "=" * 60)
    print(f"  GoFix Company Setup: {COMPANY_NAME}")
    print("=" * 60)

    print("\n── Company ──")
    create_company()

    print("\n── Warehouses ──")
    create_warehouses()

    print("\n── Company GoFix Fields ──")
    set_company_gofix_fields()

    print("\n── Company Defaults ──")
    set_company_defaults()

    print("\n── GST Tax Templates ──")
    create_gst_tax_templates()

    print("\n" + "=" * 60)
    print("  Setup complete.")
    print(f"  Verify at: /app/company/{COMPANY_NAME.replace(' ', '%20')}")
    print("=" * 60 + "\n")
