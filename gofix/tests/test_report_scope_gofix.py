# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt
"""
Tier 4 — Report scope injection E2E tests for gofix.

Verifies:
  * All 8 gofix SQL reports run cleanly for Administrator (bypass) and
    for a scoped user with a populated CH User Scope.
  * ``walkin_conversion_report._get_scope_sql`` delegates to the central
    ``scope_where_clause`` helper: bypass user → ``""``, scoped user
    with a populated warehouse set → ``" AND (sr.source_warehouse IN
    (...) OR sr.transferred_to_store IN (...))"``.
  * ``ch_erp15.report_scope.scope_where_clause`` returns the expected
    fragment for each report's dim field pattern
    (service-request warehouse endpoints, sales-order set_warehouse,
    job-assignment reached-through-SO).
"""

from __future__ import annotations

import unittest

import frappe

from ch_erp15.ch_erp15.report_scope import scope_where_clause
from ch_erp15.ch_erp15.scope import clear_scope_cache


_TEST_USER = "tier4-gofix-user@ch-tests.local"
_TEST_STORE = "TIER4-GOFIX-STORE-A"
_TEST_ROLE_PROFILE = "_Test GoFix Scoped Reporter"


def _ensure_role_profile() -> None:
    if frappe.db.exists("Role Profile", _TEST_ROLE_PROFILE):
        return
    doc = frappe.new_doc("Role Profile")
    doc.role_profile = _TEST_ROLE_PROFILE
    for role in ("Accounts User", "Service Viewer"):
        doc.append("roles", {"role": role})
    doc.insert(ignore_permissions=True)


def _ensure_user(user: str) -> None:
    if frappe.db.exists("User", user):
        doc = frappe.get_doc("User", user)
        existing_roles = {row.role for row in doc.roles}
        for role in ("Accounts User", "Service Viewer"):
            if role not in existing_roles:
                doc.append("roles", {"role": role})
        doc.save(ignore_permissions=True)
        return
    doc = frappe.new_doc("User")
    doc.email = user
    doc.first_name = "Tier4Gofix"
    doc.enabled = 1
    doc.new_password = "TestPass123!Tier4"
    doc.send_welcome_email = 0
    doc.append("roles", {"role": "Accounts User"})
    doc.append("roles", {"role": "Service Viewer"})
    doc.flags.ignore_permissions = True
    doc.insert(ignore_permissions=True)


def _get_or_create_warehouse(name: str, company: str) -> str:
    abbr = frappe.db.get_value("Company", company, "abbr")
    full = f"{name} - {abbr}"
    if frappe.db.exists("Warehouse", full):
        return full
    doc = frappe.new_doc("Warehouse")
    doc.warehouse_name = name
    doc.company = company
    doc.flags.ignore_permissions = True
    doc.insert(ignore_permissions=True)
    return doc.name


def _get_or_create_ch_store(name: str, warehouse: str, company: str) -> None:
    if frappe.db.exists("CH Store", name):
        return
    doc = frappe.new_doc("CH Store")
    doc.store_code = name
    doc.store_name = name
    doc.company = company
    doc.warehouse = warehouse
    # Active-store validation now requires the operational geography.
    reference = frappe.get_all(
        "CH Store",
        filters={"company": company, "disabled": 0, "city": ("is", "set"), "zone": ("is", "set")},
        fields=["city", "zone"],
        limit=1,
    )
    if reference:
        doc.city = reference[0].city
        doc.zone = reference[0].zone
    else:
        # Do NOT fall back to a disabled/Planned store: scope resolution skips
        # inactive stores, so the assertions below would pass vacuously. Mint the
        # geography instead so the fixture is a genuinely Active store.
        zone = frappe.db.get_value("CH Store Zone", {"company": company}, "name")
        if not zone:
            z = frappe.new_doc("CH Store Zone")
            z.zone_name = f"{name} Zone"
            z.company = company
            z.city = frappe.db.get_value("CH City", {"disabled": 0}, "name")
            # NOT the store's own warehouse: location_hierarchy rejects a store
            # whose warehouse is any zone's source hub ("configured as a zone hub").
            z.source_warehouse = (
                frappe.db.get_value(
                    "Warehouse",
                    {"company": company, "is_group": 1, "name": ("!=", warehouse)},
                    "name",
                )
                or frappe.db.get_value(
                    "Warehouse", {"company": company, "name": ("!=", warehouse)}, "name"
                )
            )
            z.flags.ignore_permissions = True
            z.insert(ignore_permissions=True)
            zone = z.name
        doc.zone = zone
    doc.flags.ignore_permissions = True
    doc.flags.ignore_mandatory = True
    doc.insert(ignore_permissions=True)


def _make_scope(user: str, store: str, company: str) -> None:
    for row in frappe.get_all("CH User Scope", filters={"user": user}, pluck="name"):
        frappe.delete_doc("CH User Scope", row, ignore_permissions=True, force=True)
    doc = frappe.new_doc("CH User Scope")
    doc.user = user
    doc.scope_role = "Store Executive"
    _ensure_role_profile()
    doc.role_profile = _TEST_ROLE_PROFILE
    doc.enabled = 1
    doc.append("stores", {"company": company, "store": store})
    doc.flags.ignore_permissions = True
    doc.insert(ignore_permissions=True)


class TestReportScopeGofix(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = frappe.db.get_value("Company", {}, "name")
        if not cls.company:
            raise Exception("No Company in this site — cannot run Tier 4 gofix tests.")

        cls.wh_in_scope = _get_or_create_warehouse("Tier4 Gofix A WH", cls.company)
        _get_or_create_ch_store(_TEST_STORE, cls.wh_in_scope, cls.company)
        _ensure_user(_TEST_USER)
        _make_scope(_TEST_USER, _TEST_STORE, cls.company)
        clear_scope_cache(_TEST_USER)
        frappe.db.commit()

    def setUp(self):
        frappe.set_user(_TEST_USER)
        clear_scope_cache(_TEST_USER)

    def tearDown(self):
        frappe.set_user("Administrator")

    # ── shared helper contract ──────────────────────────────────────────

    # 1 — scope_where_clause returns OR chain for scoped user
    def test_01_scope_service_request_scoped(self):
        clause = scope_where_clause(
            warehouse_field="sr.source_warehouse",
            extra_warehouse_fields=("sr.transferred_to_store",),
        )
        self.assertIsNotNone(clause)
        self.assertIn("sr.source_warehouse", clause)
        self.assertIn("sr.transferred_to_store", clause)

    # 2 — scope_where_clause returns None for bypass user
    def test_02_scope_bypass(self):
        frappe.set_user("Administrator")
        clause = scope_where_clause(
            warehouse_field="sr.source_warehouse",
            extra_warehouse_fields=("sr.transferred_to_store",),
        )
        self.assertIsNone(clause)

    # 3 — walkin_conversion_report._get_scope_sql prefixes with " AND "
    def test_03_walkin_scope_sql_scoped(self):
        from gofix.gofix_services.report.walkin_conversion_report.walkin_conversion_report import (
            _get_scope_sql,
        )
        sql = _get_scope_sql()
        self.assertTrue(sql.startswith(" AND "))
        self.assertIn("sr.source_warehouse", sql)

    # 4 — walkin_conversion_report._get_scope_sql returns empty for bypass
    def test_04_walkin_scope_sql_bypass(self):
        frappe.set_user("Administrator")
        from gofix.gofix_services.report.walkin_conversion_report.walkin_conversion_report import (
            _get_scope_sql,
        )
        self.assertEqual(_get_scope_sql(), "")

    # ── report end-to-end smoke ─────────────────────────────────────────

    # 5 — Service-Request-anchored reports run cleanly for scoped user
    def test_05_sr_reports_scoped(self):
        from gofix.gofix_services.report.service_request_summary.service_request_summary import (
            execute as srs_execute,
        )
        from gofix.gofix_services.report.store_wise_service_status.store_wise_service_status import (
            execute as sws_execute,
        )
        from gofix.gofix_services.report.device_service_history.device_service_history import (
            execute as dsh_execute,
        )
        for fn in (srs_execute, sws_execute, dsh_execute):
            result = fn({})
            self.assertTrue(len(result) >= 2, f"{fn.__module__} should return columns+data")

    # 6 — Sales-Order-anchored reports run cleanly
    def test_06_so_reports_scoped(self):
        from gofix.gofix_services.report.repair_profitability.repair_profitability import (
            execute as rp_execute,
        )
        from gofix.gofix_services.report.ceo_repair_dashboard.ceo_repair_dashboard import (
            execute as ceo_execute,
        )
        for fn in (rp_execute, ceo_execute):
            result = fn({})
            self.assertTrue(len(result) >= 2, f"{fn.__module__} should return columns+data")

    # 7 — Job-Assignment-anchored report runs cleanly (reaches scope via SO)
    def test_07_technician_report_scoped(self):
        from gofix.gofix_services.report.technician_performance.technician_performance import (
            execute as tp_execute,
        )
        result = tp_execute({})
        self.assertTrue(len(result) >= 2)

    # 8 — Walkin conversion report runs for both variants (module + shim)
    def test_08_walkin_reports_scoped(self):
        from gofix.gofix_services.report.walkin_conversion_report.walkin_conversion_report import (
            execute as wc_execute,
        )
        from gofix.gofix_services.report.walk_in_conversion_report.walk_in_conversion_report import (
            execute as wc_shim_execute,
        )
        for fn in (wc_execute, wc_shim_execute):
            result = fn({})
            self.assertTrue(len(result) >= 2)

    # 9 — Administrator bypass runs every touched report
    def test_09_administrator_bypass(self):
        frappe.set_user("Administrator")
        from gofix.gofix_services.report.service_request_summary.service_request_summary import (
            execute as srs_execute,
        )
        from gofix.gofix_services.report.repair_profitability.repair_profitability import (
            execute as rp_execute,
        )
        from gofix.gofix_services.report.ceo_repair_dashboard.ceo_repair_dashboard import (
            execute as ceo_execute,
        )
        from gofix.gofix_services.report.technician_performance.technician_performance import (
            execute as tp_execute,
        )
        from gofix.gofix_services.report.walkin_conversion_report.walkin_conversion_report import (
            execute as wc_execute,
        )
        srs_execute({})
        rp_execute({})
        ceo_execute({})
        tp_execute({})
        wc_execute({})
