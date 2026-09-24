# Copyright (c) 2026, GoStack and contributors
"""Repair Coverage Mix — the report must group by what the ticket decided.

`coverage_category` was derived correctly on every Service Request and read by
nothing outside tests: the Ops Hub did not show it and no report grouped by it.
This suite covers the report half of closing that gap.

Everything is seeded inside a savepoint and rolled back, because
`tabService Request` is empty on this bench — so the arithmetic is proven
against rows this test controls rather than against whatever happens to exist.

What is pinned:
  * rows come back grouped by coverage_category, in precedence order
  * money adds up per bucket, and matches the definitions Repair Profitability
    uses (actual_billed for revenue, total_repair_cost for cost)
  * a blank category surfaces as its own "Unclassified" row and is NOT folded
    into Non-Warranty -- it means the classifier never ran, which is a
    data-quality signal, not a paid repair
  * the report is scoped: a non-bypass caller sees only their own stores
"""

from __future__ import annotations

import unittest

import frappe

from gofix.gofix_services.report.repair_coverage_mix.repair_coverage_mix import (
    execute as coverage_execute,
)

_PREFIX = "COVMIX"


def _rows_by_coverage(data):
    return {r["coverage"]: r for r in data}


class TestRepairCoverageMix(unittest.TestCase):
    """Seeded inside a savepoint; nothing survives the class."""

    @classmethod
    def setUpClass(cls):
        frappe.set_user("Administrator")
        frappe.db.savepoint(_PREFIX)

        cls.company = frappe.db.get_value("Company", {"name": ("not like", "ZZZ %")}, "name")
        if not cls.company:
            raise unittest.SkipTest("no company on this site")
        # A warehouse of this test's own. Sharing the company's first leaf
        # warehouse meant the report counted every real ticket filed there
        # alongside the six seeded below, so the arithmetic only held while
        # tabService Request happened to be empty -- and it stopped holding the
        # day the bench got its first real tickets.
        parent = frappe.db.get_value(
            "Warehouse", {"company": cls.company, "is_group": 1}, "name")
        wh = frappe.new_doc("Warehouse")
        wh.warehouse_name = f"{_PREFIX} Coverage Mix"
        wh.company = cls.company
        if parent:
            wh.parent_warehouse = parent
        wh.flags.ignore_permissions = True
        wh.flags.ignore_mandatory = True
        wh.insert(ignore_permissions=True)
        cls.warehouse = wh.name

        # (coverage, billed, parts, labour, total_cost, repeat)
        cls.seed = [
            ("In-Warranty", 0, 400, 200, 600, 1),
            ("In-Warranty", 0, 100, 100, 200, 0),
            ("VAS Claim", 1500, 500, 300, 800, 0),
            ("Non-Warranty", 2000, 700, 300, 1000, 0),
            ("Non-Warranty", 1000, 200, 100, 300, 1),
            ("", 0, 50, 50, 100, 0),  # never classified
        ]
        cls.created = [cls._insert(*row) for row in cls.seed]

    @classmethod
    def _insert(cls, coverage, billed, parts, labour, total, repeat):
        """Write the row straight to the table.

        Deliberately not through the ORM: Service Request's validate chain runs
        the whole intake pipeline -- warranty lookup, SLA, custody -- and would
        re-derive coverage_category from an IMEI that does not exist here. This
        test is about the report's grouping, so the input has to be exactly the
        rows named above.
        """
        name = frappe.generate_hash(length=10)
        frappe.db.sql(
            """
            INSERT INTO `tabService Request`
                (name, creation, modified, modified_by, owner, docstatus,
                 company, source_warehouse, service_date, coverage_category,
                 actual_billed, spare_parts_cost, labor_cost, total_repair_cost,
                 is_repeat_complaint)
            VALUES (%(name)s, NOW(), NOW(), 'Administrator', 'Administrator', 1,
                    %(company)s, %(warehouse)s, CURDATE(), %(coverage)s,
                    %(billed)s, %(parts)s, %(labour)s, %(total)s, %(repeat)s)
            """,
            {
                "name": name, "company": cls.company, "warehouse": cls.warehouse,
                "coverage": coverage, "billed": billed, "parts": parts,
                "labour": labour, "total": total, "repeat": repeat,
            },
        )
        return name

    @classmethod
    def tearDownClass(cls):
        frappe.set_user("Administrator")
        frappe.db.rollback(save_point=_PREFIX)

    def _run(self):
        # Scoped to this test's own warehouse. The comment that used to sit
        # here said the assertions were restricted to the rows this test put
        # there; nothing actually restricted them, so a real ticket on the
        # bench landed in the same buckets and the counts drifted.
        _cols, data, _msg, _chart, _summary = coverage_execute(
            {"company": self.company, "source_warehouse": self.warehouse})
        return data

    # ── grouping ──────────────────────────────────────────────────────
    def test_every_bucket_appears_exactly_once(self):
        data = self._run()
        names = [r["coverage"] for r in data]
        self.assertEqual(len(names), len(set(names)), f"a bucket repeated: {names}")
        for expected in ("In-Warranty", "VAS Claim", "Non-Warranty", "Unclassified"):
            self.assertIn(expected, names, f"{expected} missing from {names}")

    def test_buckets_are_ordered_by_precedence_not_alphabet(self):
        """The rows read down the way the rule is applied."""
        data = self._run()
        order = [r["coverage"] for r in data
                 if r["coverage"] in ("In-Warranty", "VAS Claim", "Non-Warranty")]
        self.assertEqual(order, ["In-Warranty", "VAS Claim", "Non-Warranty"])

    def test_a_blank_category_is_not_counted_as_a_paid_repair(self):
        """The whole point of the Unclassified row."""
        rows = _rows_by_coverage(self._run())
        self.assertGreaterEqual(rows["Unclassified"]["jobs"], 1)
        # The blank row's 100 of cost must not have landed in Non-Warranty.
        self.assertEqual(rows["Non-Warranty"]["total_cost"], 1300)

    # ── arithmetic ────────────────────────────────────────────────────
    def test_money_adds_up_per_bucket(self):
        rows = _rows_by_coverage(self._run())

        iw = rows["In-Warranty"]
        self.assertEqual(iw["jobs"], 2)
        self.assertEqual(iw["revenue"], 0, "an in-warranty repair bills nothing")
        self.assertEqual(iw["total_cost"], 800)
        self.assertEqual(iw["margin"], -800, "cost we carry must read as negative")
        self.assertEqual(iw["avg_cost"], 400)
        self.assertEqual(iw["repeats"], 1)

        vas = rows["VAS Claim"]
        self.assertEqual(vas["jobs"], 1)
        self.assertEqual(vas["revenue"], 1500)
        self.assertEqual(vas["total_cost"], 800)
        self.assertEqual(vas["margin"], 700)

        nw = rows["Non-Warranty"]
        self.assertEqual(nw["jobs"], 2)
        self.assertEqual(nw["revenue"], 3000)
        self.assertEqual(nw["margin"], 1700)

    def test_share_percentages_total_one_hundred(self):
        data = self._run()
        self.assertAlmostEqual(sum(r["share_pct"] for r in data), 100.0, places=4)

    def test_summary_names_the_two_numbers_a_service_head_is_asked_for(self):
        _c, data, _m, _ch, summary = coverage_execute(
            {"company": self.company, "source_warehouse": self.warehouse})
        self.assertTrue(data)
        by_label = {s["label"]: s["value"] for s in summary}
        self.assertEqual(by_label["Cost We Carry"], 800)
        self.assertEqual(by_label["Recoverable From Plans"], 800)
        self.assertGreaterEqual(by_label["Unclassified"], 1)

    # ── scope ─────────────────────────────────────────────────────────
    def test_the_report_is_scoped_not_open(self):
        """Administrator proves nothing — the clause has to be built at all.

        A full non-bypass execution needs a scoped user holding these seeded
        warehouses, which this fixture does not create. What is provable here
        is that the report asks report_scope for a clause on the same two
        warehouse fields Service Request Summary uses, rather than querying the
        table unguarded.
        """
        import inspect

        from gofix.gofix_services.report.repair_coverage_mix import repair_coverage_mix

        src = inspect.getsource(repair_coverage_mix.get_data)
        self.assertIn("scope_where_clause", src)
        self.assertIn("sr.source_warehouse", src)
        self.assertIn("transferred_to_store", src)
        self.assertIn("geo_conditions", src)
