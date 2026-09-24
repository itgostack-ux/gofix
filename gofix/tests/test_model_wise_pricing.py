"""Pricing narrows by device: category, sub category, brand, model.

A laptop is not a phone, an Apple is not an Android, and an iPhone 15 Pro Max
is not an iPhone 11. These pin that the most specific rule wins, that a rule
written for one scope never prices a device outside it, and -- the part that
matters most on a live bench -- that the 148 brand rules already in use keep
behaving exactly as they did.
"""

import unittest

import frappe

from gofix.gofix_services.doctype.gofix_pricing_rule import gofix_pricing_rule as pr


class TestDeviceAxis(unittest.TestCase):
    """Every level is derived from the one below when not supplied."""

    def setUp(self):
        frappe.set_user("Administrator")
        self.sp = "device_axis_test"
        frappe.db.savepoint(self.sp)

    def tearDown(self):
        frappe.db.rollback(save_point=self.sp)

    def test_a_model_yields_its_brand_sub_category_and_category(self):
        row = frappe.db.sql(
            """SELECT m.name, m.brand, sc.name AS sub, sc.category
               FROM `tabCH Model` m JOIN `tabCH Sub Category` sc ON sc.name = m.sub_category
               WHERE COALESCE(m.brand,'') <> '' AND COALESCE(sc.category,'') <> '' LIMIT 1""",
            as_dict=True,
        )
        if not row:
            raise unittest.SkipTest("no CH Model with a brand and category on this site")
        row = row[0]
        axis = pr.device_axis(device_model=row.name)
        self.assertEqual(axis["device_model"], row.name)
        self.assertEqual(axis["device_brand"], row.brand)
        self.assertEqual(axis["device_sub_category"], row.sub)
        self.assertEqual(axis["device_category"], row.category)

    def test_knowing_nothing_is_not_an_error(self):
        """A free-text intake must still price, with every device rule a wildcard."""
        self.assertEqual(
            pr.device_axis(),
            {"device_model": None, "device_brand": None,
             "device_sub_category": None, "device_category": None},
        )


class TestSpecificityLadder(unittest.TestCase):
    def setUp(self):
        frappe.set_user("Administrator")
        self.sp = "ladder_test"
        frappe.db.savepoint(self.sp)
        base = frappe.db.sql(
            """SELECT name, repair_solution, issue_category, device_brand, labor_rate
               FROM `tabGoFix Pricing Rule`
               WHERE is_active = 1 AND COALESCE(device_brand,'') <> ''
                 AND COALESCE(repair_solution,'') <> '' LIMIT 1""",
            as_dict=True,
        )
        if not base:
            raise unittest.SkipTest("no brand-scoped pricing rule on this site")
        self.base = base[0]
        models = frappe.db.sql(
            """SELECT name FROM `tabCH Model` WHERE brand = %s LIMIT 2""",
            self.base.device_brand, pluck=True,
        )
        if len(models) < 2:
            raise unittest.SkipTest("need two models of the same brand to prove narrowing")
        self.model, self.other_model = models[0], models[1]

    def tearDown(self):
        frappe.db.rollback(save_point=self.sp)

    def _rate(self, **kw):
        rule = pr.get_pricing_rule(
            issue_category=self.base.issue_category,
            repair_solution=self.base.repair_solution, **kw)
        return (rule.name, rule.labor_rate) if rule else (None, None)

    def _rule(self, **fields):
        doc = frappe.new_doc("GoFix Pricing Rule")
        doc.update({"rule_name": "ZZ ladder test", "is_active": 1,
                    "labor_rate_type": "Fixed", **fields})
        doc.flags.ignore_permissions = True
        doc.insert(ignore_permissions=True)
        return doc

    def test_an_existing_brand_rule_still_prices_every_model_of_that_brand(self):
        """The whole point of the fallback: nothing that worked stops working."""
        name, rate = self._rate(brand=self.base.device_brand, device_model=self.model)
        self.assertEqual(name, self.base.name)
        self.assertEqual(rate, self.base.labor_rate)

    def test_a_model_rule_beats_the_brand_rule(self):
        self._rule(issue_category=self.base.issue_category,
                   repair_solution=self.base.repair_solution,
                   device_model=self.model, labor_rate=9999)
        _, rate = self._rate(brand=self.base.device_brand, device_model=self.model)
        self.assertEqual(rate, 9999)

    def test_a_model_rule_does_not_leak_onto_other_models(self):
        """The failure that would quietly overcharge every device of a brand."""
        self._rule(issue_category=self.base.issue_category,
                   repair_solution=self.base.repair_solution,
                   device_model=self.model, labor_rate=9999)
        _, rate = self._rate(brand=self.base.device_brand, device_model=self.other_model)
        self.assertEqual(rate, self.base.labor_rate)

    def test_a_rule_for_another_category_never_prices_this_device(self):
        """A rule written for laptops must refuse a phone, not merely score low."""
        foreign = frappe.db.get_value("CH Category", {"name": ("!=", "Smart Phones")}, "name")
        if not foreign:
            raise unittest.SkipTest("only one CH Category on this site")
        rule = self._rule(issue_category=self.base.issue_category,
                          repair_solution=self.base.repair_solution,
                          device_category=foreign, labor_rate=4321)
        name, _ = self._rate(brand=self.base.device_brand, device_model=self.model)
        self.assertNotEqual(name, rule.name)


class TestBenchFeeLadder(unittest.TestCase):
    def setUp(self):
        frappe.set_user("Administrator")
        self.sp = "bench_fee_test"
        frappe.db.savepoint(self.sp)

    def tearDown(self):
        frappe.db.rollback(save_point=self.sp)

    def test_with_no_device_rule_the_item_price_still_decides(self):
        from gofix.setup.gofix_service_charge_item import get_service_charge
        expected = (get_service_charge() or {}).get("rate")
        if not expected:
            raise unittest.SkipTest("GOFIX-SERVICE-CHARGE has no price on this site")
        self.assertEqual(pr.resolve_service_charge().get("rate"), expected)

    def test_a_category_rule_sets_the_fee_for_that_category_only(self):
        cats = frappe.get_all("CH Category", pluck="name", limit=2)
        if len(cats) < 2:
            raise unittest.SkipTest("need two CH Categories")
        doc = frappe.new_doc("GoFix Pricing Rule")
        doc.update({"rule_name": "ZZ bench fee test", "is_active": 1,
                    "device_category": cats[0], "service_charge": 750,
                    "labor_rate_type": "Fixed"})
        doc.flags.ignore_permissions = True
        doc.insert(ignore_permissions=True)

        from gofix.setup.gofix_service_charge_item import get_service_charge
        default = (get_service_charge() or {}).get("rate")
        self.assertEqual(pr.resolve_service_charge(device_category=cats[0]).get("rate"), 750)
        self.assertEqual(pr.resolve_service_charge(device_category=cats[1]).get("rate"), default)

    def test_a_repair_specific_rule_never_decides_the_bench_fee(self):
        """The fee is for handling the device, not for doing a repair.

        A rule that names a solution is about that repair; letting it set the
        bench fee would charge a different handling fee depending on which
        repair happened to be quoted.
        """
        cat = frappe.get_all("CH Category", pluck="name", limit=1)
        sol = frappe.db.get_value("GoFix Pricing Rule", {"repair_solution": ("!=", "")}, "repair_solution")
        if not (cat and sol):
            raise unittest.SkipTest("need a category and a solution-scoped rule")
        doc = frappe.new_doc("GoFix Pricing Rule")
        doc.update({"rule_name": "ZZ solution bench fee", "is_active": 1,
                    "device_category": cat[0], "repair_solution": sol,
                    "service_charge": 4242, "labor_rate_type": "Fixed"})
        doc.flags.ignore_permissions = True
        doc.insert(ignore_permissions=True)
        self.assertNotEqual(pr.resolve_service_charge(device_category=cat[0]).get("rate"), 4242)
