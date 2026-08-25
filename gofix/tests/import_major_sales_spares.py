# Copyright (c) 2026, GoFix and contributors
# One-off TEST import: "Major sales SKUs with Category.xlsx" -> real spare
# Item records, following the same CH Category -> CH Sub Category -> CH Model
# -> Item chain used by every other spare in this system (see
# ch_item_master/overrides/item.py before_insert + CHModel/CHSubCategory).
#
# This is a test-flow script, NOT the production bulk-import path. It exists
# so records + the GoFix spare<->model compatibility flow can be reviewed on
# erpnext.local before the real production upload.
#
# Idempotent: re-running skips Items/Sub Categories/Models that already exist.
#
# Run:
#   bench --site erpnext.local execute gofix.tests.import_major_sales_spares.run

import json
import re

import frappe
from frappe.utils import flt

EXCEL_PATH = "/home/palla/erpnext-bench/Major sales SKUs with Category.xlsx"
REPORT_PATH = "/tmp/major_sales_spares_import_report.json"

EXPECTED_HEADER = [
	"Item Code", "Name", "Model", "Brand", "Category", "Quality", "Service Price", "Grade",
]

# Excel "Category" text -> real CH Category (all three already exist).
CATEGORY_ALIASES = {
	"mobile spares": "Mobile Spares",
	"mobiles - 85171300": "Mobile Spares",
	"additional - mob": "Mobile Spares",
	"laptop spares": "Laptop Spares",
	"tablet spares": "Tablet Spares",
}

# Excel "Brand" text -> (canonical Brand name, Manufacturer name).
# Verified against existing CH Model rows: self-named manufacturer exists for
# every brand here. "Google Pixel" has no separate Brand master — it maps to
# the existing "Google" brand.
BRAND_MAP = {
	"acer": ("Acer", "Acer"),
	"apple": ("Apple", "Apple"),
	"google pixel": ("Google", "Google"),
	"hp": ("HP", "HP"),
	"iqoo": ("iQOO", "Iqoo"),
	"lenovo": ("Lenovo", "Lenovo"),
	"motorola": ("Motorola", "Motorola"),
	"nothing": ("Nothing", "Nothing"),
	"oneplus": ("Oneplus", "Oneplus"),
	"oppo": ("Oppo", "Oppo"),
	"poco": ("Poco", "Poco"),
	"realme": ("Realme", "Realme"),
	"redmi": ("Redmi", "Redmi"),
	"samsung": ("Samsung", "Samsung"),
	"vivo": ("Vivo", "Vivo"),
	"xiaomi": ("Xiaomi", "Xiaomi"),
}

# Part-type phrase (from the Name column, after the model prefix and quality
# suffix are stripped) -> (CH Sub Category name, default GST HSN Code).
# Checked in order; first substring match wins. Reuses the 3 sub-categories
# that already exist under Mobile Spares (Batteries/Displays/Rear Cameras).
SUBCATEGORY_RULES = [
	(["with frame display", "display"], "Displays", "85177090"),
	(["battery"], "Batteries", "85076000"),
	(["rear camera"], "Rear Cameras", "85177090"),
	(["front camera"], "Front Cameras", "85177090"),
	(["camera lens"], "Camera Lens", "85177090"),
	(["back glass"], "Back Glass", "85177090"),
	(["back door"], "Back Door", "85177090"),
	(["outer keys"], "Outer Keys", "85177090"),
	(["inner strip"], "Inner Strip", "85177090"),
	(["charging board"], "Charging Board", "85177090"),
	(["charging strip"], "Charging Strip", "85177090"),
	(["charging pin"], "Charging Pin", "85177090"),
	(["ear speaker", "earing speaker"], "Ear Speaker", "85177090"),
	(["swapping board"], "Swapping Board", "85177090"),
	(["volume strip"], "Volume Strip", "85177090"),
	(["ringer"], "Ringer", "85177090"),
	(["housing set", "housing"], "Housing Set", "85177090"),
	(["sim tray"], "Sim Tray", "85177090"),
	(["finger print"], "Finger Print Sensor", "85177090"),
	(["sub board strip"], "Sub Board Strip", "85177090"),
	(["network strip"], "Network Strip", "85177090"),
	(["loud speaker", "speaker"], "Speaker", "85177090"),
	(["ab panel"], "AB Panel", "85177090"),
	(["on / off strip"], "Power Button Strip", "85177090"),
	(["mother board"], "Motherboard", "85177090"),
	(["frame"], "Frame", "85177090"),
	(["keyboard"], "Keyboard", "85177090"),
]


def _part_type_phrase(name, quality):
	"""Extract the descriptive part-type text from a Name cell.

	'Acer Nitro 5 - Battery OEM' + quality='OEM' -> 'Battery'
	"""
	remainder = name.split(" - ", 1)[1].strip() if " - " in name else name.strip()
	if quality:
		remainder = re.sub(re.escape(quality) + r"\s*$", "", remainder, flags=re.IGNORECASE).strip()
	return remainder


def _classify_subcategory(phrase):
	low = phrase.lower()
	for substrings, sub_category_name, hsn in SUBCATEGORY_RULES:
		if any(s in low for s in substrings):
			return sub_category_name, hsn
	# Safety net for any phrase outside the 64 seen during analysis.
	return phrase.strip().title()[:100] or "Other Spares", "85177090"


def _norm(s):
	return re.sub(r"\s+", " ", (s or "").strip().lower())


def _ensure_sub_category(category, sub_category_name, hsn_code):
	full_name = f"{category}-{sub_category_name}"
	if frappe.db.exists("CH Sub Category", full_name):
		return full_name
	doc = frappe.new_doc("CH Sub Category")
	doc.category = category
	doc.sub_category_name = sub_category_name
	doc.prefix = "SP"  # matches the prefix already used by every spares sub-category
	doc.item_nature = "Simple Custom-Named"  # same nature as existing spare sub-categories
	doc.default_uom = "Nos"
	doc.is_stock_item_default = 1
	doc.hsn_code = hsn_code
	doc.gst_rate = 18.0
	doc.status = "Active"
	doc.insert(ignore_permissions=True)
	return doc.name


def _ensure_model(sub_category, brand, manufacturer, model_name):
	full_name = f"{sub_category}-{brand}-{model_name}"
	if frappe.db.exists("CH Model", full_name):
		return full_name
	doc = frappe.new_doc("CH Model")
	doc.sub_category = sub_category
	doc.manufacturer = manufacturer
	doc.brand = brand
	doc.model_name = model_name
	doc.status = "Active"
	doc.insert(ignore_permissions=True)
	return doc.name


def _load_device_template_map():
	"""normalized item_name -> Item code, for existing non-stock device templates."""
	rows = frappe.get_all(
		"Item",
		filters={"is_stock_item": 0, "item_group": ["in", ["Mobiles", "Laptops", "Tablets"]]},
		fields=["name", "item_name"],
	)
	m = {}
	for r in rows:
		key = _norm(r.item_name)
		if key not in m:
			m[key] = r.name
	return m


def run():
	import openpyxl

	wb = openpyxl.load_workbook(EXCEL_PATH, data_only=True)
	ws = wb.active
	rows = list(ws.iter_rows(values_only=True))
	header, data_rows = rows[0], rows[1:]
	header = [str(h).strip() if h else "" for h in header]
	if header != EXPECTED_HEADER:
		frappe.throw(f"Unexpected header {header}, expected {EXPECTED_HEADER}")

	device_template_map = _load_device_template_map()

	created = []
	skipped_existing = []
	skipped_bad_category = []
	failed = []
	unmatched_compatibility = []
	autogen_counter = 0

	for i, row in enumerate(data_rows, start=2):
		item_code, name, model_col, brand_raw, category_raw, quality, price_raw, grade = (
			list(row) + [None] * (8 - len(row))
		)[:8]

		name = (name or "").strip()
		if not name or price_raw in (None, ""):
			failed.append({"row": i, "error": "missing Name/Service Price"})
			continue
		try:
			price_value = flt(price_raw)
		except (TypeError, ValueError):
			price_value = 0
		if price_value <= 0:
			failed.append({"row": i, "name": name, "error": f"invalid Service Price {price_raw!r}"})
			continue

		category = CATEGORY_ALIASES.get(_norm(category_raw))
		if not category:
			skipped_bad_category.append({"row": i, "name": name, "category": category_raw})
			continue

		brand_info = BRAND_MAP.get(_norm(brand_raw))
		if not brand_info:
			failed.append({"row": i, "error": f"unmapped brand {brand_raw!r}"})
			continue
		brand, manufacturer = brand_info

		item_code = (item_code or "").strip()
		if not item_code:
			autogen_counter += 1
			item_code = f"SP-AUTOGEN-{autogen_counter:04d}"

		if frappe.db.exists("Item", item_code):
			skipped_existing.append(item_code)
			continue

		savepoint = f"major_sales_spares_row_{i}"
		frappe.db.savepoint(savepoint)
		try:
			phrase = _part_type_phrase(name, (quality or "").strip())
			sub_category_name, hsn_code = _classify_subcategory(phrase)
			sub_category = _ensure_sub_category(category, sub_category_name, hsn_code)
			model = _ensure_model(sub_category, brand, manufacturer, name)

			device_item = device_template_map.get(_norm(model_col))
			if not device_item:
				unmatched_compatibility.append({"row": i, "name": name, "model": model_col})

			quality_s = (quality or "").strip()
			grade_s = (grade or "").strip()
			description = (
				f"{name} | Quality: {quality_s or '-'} | Grade: {grade_s or '-'} | "
				f"Compatible Model (source): {model_col or '-'} | "
				f"Source: Major Sales SKUs sheet"
			)

			item = frappe.new_doc("Item")
			item.item_code = item_code
			item.item_name = name
			item.ch_model = model
			item.ch_sub_category = sub_category
			item.ch_category = category
			item.item_group = "Spares"
			item.brand = brand
			item.stock_uom = "Nos"
			item.is_stock_item = 1
			item.is_sales_item = 1
			item.is_purchase_item = 1
			item.standard_rate = price_value
			# ch_item_master requires ch_item_mrp > 0 for every stock item. The
			# sheet has no separate MRP column, so Service Price is used as MRP
			# too (test-flow assumption — flag for review before production).
			item.ch_item_mrp = price_value
			item.gst_hsn_code = hsn_code
			item.description = description
			if device_item:
				item.append("gofix_compatible_models", {"device_model": device_item})
			item.insert(ignore_permissions=True)
			created.append(item.name)
		except Exception as e:
			frappe.db.rollback(save_point=savepoint)
			failed.append({"row": i, "name": name, "error": str(e)})

	frappe.db.commit()

	report = {
		"total_rows": len(data_rows),
		"created": len(created),
		"skipped_existing": len(skipped_existing),
		"skipped_bad_category": skipped_bad_category,
		"failed": failed,
		"unmatched_compatibility_count": len(unmatched_compatibility),
		"unmatched_compatibility_sample": unmatched_compatibility[:50],
	}
	with open(REPORT_PATH, "w") as f:
		json.dump(report, f, indent=2)

	print("\n=== Major Sales SKUs Import (TEST) ===")
	print(f"Total rows          : {len(data_rows)}")
	print(f"Created             : {len(created)}")
	print(f"Skipped (existing)  : {len(skipped_existing)}")
	print(f"Skipped (bad cat.)  : {len(skipped_bad_category)}")
	print(f"Failed              : {len(failed)}")
	print(f"No compatibility link (model not found as device Item): {len(unmatched_compatibility)}")
	print(f"Full report written to {REPORT_PATH}")
	return report
