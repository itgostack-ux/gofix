# Copyright (c) 2026, GoFix and contributors
# E2E test: Spare ↔ Device Model compatibility.
#
# Verifies that:
#   1. The "Compatible Device Models" child table on Item drives compatibility.
#   2. is_spare_compatible_with_device() honours universal vs restricted spares.
#   3. get_compatible_spare_items() only offers spares that fit the device.
#   4. Spare Parts Usage.validate blocks an incompatible spare and allows a
#      compatible / universal one.
#
# Run:
#   bench --site erpnext.local execute gofix.tests.test_spare_model_compatibility.run

import frappe
from frappe.utils import random_string

from gofix.gofix_services.api import (
	is_spare_compatible_with_device,
	get_compatible_spare_items,
)

_results = []


def _ok(step, detail=""):
	_results.append(("PASS", step))
	print(f"  PASS  {step}" + (f"  ({detail})" if detail else ""))


def _fail(step, detail=""):
	_results.append(("FAIL", step))
	print(f"  FAIL  {step}" + (f"  — {detail}" if detail else ""))


def _assert(cond, step, detail=""):
	if cond:
		_ok(step, detail)
	else:
		_fail(step, detail)


# ── fixtures ────────────────────────────────────────────────────────────────

def _ensure_item_group(name, parent="All Item Groups"):
	if not frappe.db.exists("Item Group", name):
		doc = frappe.new_doc("Item Group")
		doc.item_group_name = name
		doc.parent_item_group = parent
		doc.is_group = 0
		doc.insert(ignore_permissions=True)
	return name


def _ensure_item(code, item_name, group, is_stock=1):
	if frappe.db.exists("Item", code):
		return code
	doc = frappe.new_doc("Item")
	doc.item_code = code
	doc.item_name = item_name
	doc.item_group = group
	doc.stock_uom = "Nos"
	doc.is_stock_item = is_stock
	doc.is_sales_item = 1
	doc.insert(ignore_permissions=True)
	return code


def _set_compatibility(spare_code, device_codes):
	"""Replace the compatible-models table on a spare Item."""
	item = frappe.get_doc("Item", spare_code)
	item.set("gofix_compatible_models", [])
	for dev in device_codes:
		item.append("gofix_compatible_models", {"device_model": dev})
	item.save(ignore_permissions=True)


def run():
	print("\n=== GoFix Spare ↔ Device Model Compatibility E2E ===\n")

	# Make sure the Item custom field exists even on a fresh checkout.
	from gofix.setup.item_custom_fields import create_item_custom_fields
	create_item_custom_fields()
	frappe.clear_cache(doctype="Item")

	if not frappe.db.exists("DocType", "GoFix Spare Compatible Model"):
		_fail("child DocType exists", "GoFix Spare Compatible Model missing — run bench migrate first")
		return _summary()

	if not frappe.db.exists("Custom Field", {"dt": "Item", "fieldname": "gofix_compatible_models"}):
		_fail("custom field exists", "Item.gofix_compatible_models missing")
		return _summary()
	_ok("custom field + child DocType present")

	created = []
	sp = frappe.db.savepoint("spare_compat_test")
	try:
		dev_group = _ensure_item_group("GoFix Test Devices")
		spare_group = _ensure_item_group("Spare Parts")  # used by the Ops Hub filter

		tag = random_string(5).upper()
		d1 = _ensure_item(f"GFTST-DEV-A-{tag}", "Test Device A", dev_group, is_stock=0)
		d2 = _ensure_item(f"GFTST-DEV-B-{tag}", "Test Device B", dev_group, is_stock=0)
		s_a = _ensure_item(f"GFTST-SPARE-A-{tag}", "Test Spare A (fits A)", spare_group)
		s_b = _ensure_item(f"GFTST-SPARE-B-{tag}", "Test Spare B (fits B)", spare_group)
		s_u = _ensure_item(f"GFTST-SPARE-U-{tag}", "Test Spare U (universal)", spare_group)
		created += [d1, d2, s_a, s_b, s_u]

		_set_compatibility(s_a, [d1])
		_set_compatibility(s_b, [d2])
		# s_u left with no rows → universal

		# 1. Helper semantics ------------------------------------------------
		_assert(is_spare_compatible_with_device(s_a, d1) is True, "spare A compatible with device A")
		_assert(is_spare_compatible_with_device(s_a, d2) is False, "spare A NOT compatible with device B")
		_assert(is_spare_compatible_with_device(s_b, d1) is False, "spare B NOT compatible with device A")
		_assert(is_spare_compatible_with_device(s_u, d1) is True, "universal spare compatible with device A")
		_assert(is_spare_compatible_with_device(s_u, d2) is True, "universal spare compatible with device B")

		# 2. Link query ------------------------------------------------------
		rows = get_compatible_spare_items(
			"Item", "", "name", 0, 50,
			{"device_item": d1, "item_group": spare_group},
		)
		names = {r[0] for r in rows}
		_assert(s_a in names, "query offers compatible spare A for device A")
		_assert(s_u in names, "query offers universal spare for device A")
		_assert(s_b not in names, "query hides incompatible spare B for device A")

		# 3. Spare Parts Usage guard ----------------------------------------
		_test_spu_guard(d1, s_a, s_b, s_u, created)

	finally:
		frappe.db.rollback(save_point="spare_compat_test")
		# Hard-clean any rows that escaped the savepoint (defensive).
		for code in created:
			if frappe.db.exists("Item", code):
				try:
					frappe.delete_doc("Item", code, force=1, ignore_permissions=True)
				except Exception:
					pass
		frappe.db.commit()

	return _summary()


def _test_spu_guard(device, compatible_spare, incompatible_spare, universal_spare, created):
	"""Build a minimal SR and assert the SPU compatibility guard fires."""
	company = frappe.defaults.get_global_default("company") or frappe.db.get_value("Company", {}, "name")
	customer = frappe.db.get_value("Customer", {}, "name")
	warehouse = frappe.db.get_value("Warehouse", {"is_group": 0, "disabled": 0}, "name")
	if not (company and customer and warehouse):
		_fail("SPU guard setup", "missing company/customer/warehouse master data")
		return

	sr = frappe.new_doc("Service Request")
	sr.flags.ignore_mandatory = True
	sr.flags.ignore_permissions = True
	sr.customer = customer
	sr.device_item = device
	sr.device_item_name = "Test Device A"
	sr.source_warehouse = warehouse
	sr.company = company
	sr.insert(ignore_permissions=True, ignore_mandatory=True)
	created_sr = sr.name

	def _make_spu(spare):
		spu = frappe.new_doc("Spare Parts Usage")
		spu.service_request = created_sr
		spu.spare_part_item = spare
		spu.qty_used = 1
		spu.transaction_date = frappe.utils.today()
		return spu

	# Incompatible spare → guard must raise.
	threw = False
	try:
		_make_spu(incompatible_spare).validate_device_compatibility()
	except frappe.ValidationError:
		threw = True
	_assert(threw, "SPU guard blocks incompatible spare")

	# Compatible + universal → guard must pass silently.
	passed = True
	try:
		_make_spu(compatible_spare).validate_device_compatibility()
		_make_spu(universal_spare).validate_device_compatibility()
	except frappe.ValidationError:
		passed = False
	_assert(passed, "SPU guard allows compatible & universal spares")


def _summary():
	passed = sum(1 for s, _ in _results if s == "PASS")
	failed = sum(1 for s, _ in _results if s == "FAIL")
	print(f"\n=== Result: {passed} passed, {failed} failed ===\n")
	return {"passed": passed, "failed": failed}
