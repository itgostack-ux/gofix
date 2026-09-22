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
            # Build a warehouse for the hub instead of hunting for one.
            # location_hierarchy._validate_hub_candidate refuses a candidate that
            # is a group, disabled, a store bin, owned by a CH Store, typed
            # anything but "Zone Warehouse", or — the one that bit us — a Transit
            # warehouse. Asking for "the first ledger warehouse of this company"
            # satisfied only the first of those, so on a company whose first ledger
            # warehouse is Goods In Transit the whole module aborted in setUpClass.
            # A freshly created Warehouse carries none of those attributes.
            z.source_warehouse = _get_or_create_warehouse(f"{name} Zone Hub", company)
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
        cls.company = (
            frappe.defaults.get_defaults().get("company")
            # Undefined MariaDB order otherwise: once a sibling suite has left a
            # ZZZ-prefixed test company behind, this picked that company and
            # every warehouse/zone fixture below was built under a shell that
            # has no ledger warehouses.
            or frappe.db.get_value("Company", {"name": ("not like", "ZZZ %")}, "name")
        )
        if not cls.company:
            raise Exception("No Company in this site — cannot run Tier 4 gofix tests.")

        cls.wh_in_scope = _get_or_create_warehouse("Tier4 Gofix A WH", cls.company)
        _get_or_create_ch_store(_TEST_STORE, cls.wh_in_scope, cls.company)
        # Before _ensure_user, not after. This module commits its fixtures, so
        # the test user survives between runs carrying role_profile_name; if the
        # profile itself has since gone (another suite, a cleanup, a rewrite of
        # the site's Role Profiles), saving that user throws LinkValidationError
        # on a dangling link and setUpClass dies before _make_scope -- which is
        # the only thing that would have recreated the profile. The module had
        # been erroring at zero tests run for exactly that reason.
        _ensure_role_profile()
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
    #
    # The report moved to ch_pos when POS Kiosk Token became the single walk-in
    # token for both companies; the gofix copy and its shim were deleted. It is
    # anchored on the token (t.store / t.pos_profile), not on a Service Request.
    def test_03_walkin_scope_sql_scoped(self):
        from ch_pos.pos_core.report.walkin_conversion_report.walkin_conversion_report import (
            _get_scope_sql,
        )
        sql = _get_scope_sql()
        self.assertTrue(sql.startswith(" AND "))
        self.assertIn("t.store", sql)

    # 4 — walkin_conversion_report._get_scope_sql returns empty for bypass
    def test_04_walkin_scope_sql_bypass(self):
        frappe.set_user("Administrator")
        from ch_pos.pos_core.report.walkin_conversion_report.walkin_conversion_report import (
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

    # 8 — Walkin conversion report runs scoped (one report now, no shim)
    def test_08_walkin_reports_scoped(self):
        from ch_pos.pos_core.report.walkin_conversion_report.walkin_conversion_report import (
            execute as wc_execute,
        )
        result = wc_execute({})
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
        from ch_pos.pos_core.report.walkin_conversion_report.walkin_conversion_report import (
            execute as wc_execute,
        )
        srs_execute({})
        rp_execute({})
        ceo_execute({})
        tp_execute({})
        wc_execute({})

    # 10 — every gofix report executes for a scoped, non-bypass user
    def test_10_all_gofix_reports_execute_for_scoped_user(self):
        """The whole report surface, not the handful named above.

        gofix_first_time_fix_rate and device_service_history collect their
        predicates in a *list* and were doing ``conditions += geo_conditions(...)``
        — a string, appended one character at a time, producing
        ``... AND A AND N AND D ...``. It raised a SQL syntax error for every
        scoped user and nothing at all for Administrator, because the scope
        clause is empty for a bypass caller. A per-report smoke run as the
        scoped user is the only thing that sees it.
        """
        import importlib
        import pkgutil

        import gofix.gofix_services.report as report_pkg

        failures = []
        checked = 0
        for mod in pkgutil.iter_modules(report_pkg.__path__):
            if not mod.ispkg:
                continue
            path = f"{report_pkg.__name__}.{mod.name}.{mod.name}"
            try:
                execute = importlib.import_module(path).execute
            except (ModuleNotFoundError, AttributeError):
                continue  # a report with no python module (Query/Report Builder)
            checked += 1
            try:
                execute({})
            except Exception as exc:  # noqa: BLE001 — we want the report named
                failures.append(f"{mod.name}: {type(exc).__name__}: {exc}"[:300])

        self.assertTrue(checked, "No gofix report modules were discovered.")
        self.assertFalse(
            failures,
            "Reports that fail for a scoped user (Administrator would not see "
            f"these):\n  " + "\n  ".join(failures),
        )
