"""Seed the GoFix service-chain masters that sit behind Issue Category.

The issue → solution → spare cascade, technician skill matching, QC checklist
auto-population and SLA/approval routing are all live code paths
(``gofix_services.api.get_solutions_for_issues`` /
``get_eligible_technicians``, ``overrides.sales_order._populate_qc_checklist``,
``gofix_sla_rule.check_gofix_sla_breach``) — but none of their masters were
ever seeded, so every consumer returned empty. This patch loads:

* Repair Solution        — canonical solution catalogue per Issue Category
* Technician Grade       — L1/L2/L3/L4 ladder with a per-category skill matrix
* Walkin Source          — Service Request lead-source options
* Withdrawal Reason      — customer-withdrawal catalogue
* GoFix QC Template      — generic post-repair QC + category-specific packs
* GoFix SLA / Approval Rules — via ``gofix.setup.seed_default_rules``

Deliberately NOT seeded (ops/company data, not master data):

* Solution Spare Mapping & GoFix Spare Compatible Model — they link real
  spare-part Items with prices and must be mapped explicitly per tenant.
* GoFix Repair Cost Template / GoFix Pricing Rule — per-company pricing.
* Service Order State / Transition — legacy of the pre-Frappe-Workflow state
  machine; the live workflow ships as fixtures.

Idempotent: parents are upserted; child tables (grade skills, QC checks) only
gain missing rows on re-run so ops tuning is never clobbered.
"""

from __future__ import annotations

import frappe

from gofix.patches.seed_gofix_token_masters import _upsert


# ---------------------------------------------------------------------------
# Technician grade ladder
# ---------------------------------------------------------------------------

_GRADES = [
	{"grade_name": "L1 - Junior Technician", "grade_level": 1, "skill": "Basic",
	 "description": "Front-bench repairs: screens, batteries, panels, software."},
	{"grade_name": "L2 - Senior Technician", "grade_level": 2, "skill": "Intermediate",
	 "description": "Intermediate repairs: ports, cameras, audio, buttons and guided component swaps."},
	{"grade_name": "L3 - Lead Technician", "grade_level": 3, "skill": "Advanced",
	 "description": "Advanced component-level work: board diagnosis, sensors, network modules, frames and water treatment."},
	{"grade_name": "L4 - Expert Technician", "grade_level": 4, "skill": "Expert",
	 "description": "Expert work: board-level repair, liquid-damage component recovery and advanced data recovery."},
]

_GRADE_FOR_SKILL = {
	"Basic": "L1 - Junior Technician",
	"Intermediate": "L2 - Senior Technician",
	"Advanced": "L3 - Lead Technician",
	"Expert": "L4 - Expert Technician",
}

_LEGACY_GRADE_RENAMES = {
	"L3 - Expert Technician": "L4 - Expert Technician",
}


# ---------------------------------------------------------------------------
# Repair Solution catalogue: (name, category, code, minutes, requires_spare, skill)
# ---------------------------------------------------------------------------

_SOLUTIONS = [
	("Screen Replacement",              "Screen & Display",       "SCR-REP", 90,  1, "Basic"),
	("Touch Glass Replacement",         "Screen & Display",       "SCR-TGL", 90,  1, "Intermediate"),
	("Display Diagnosis",               "Screen & Display",       "SCR-DIA", 30,  0, "Basic"),
	("Battery Replacement",             "Battery",                "BAT-REP", 45,  1, "Basic"),
	("Battery Health Diagnosis",        "Battery",                "BAT-DIA", 20,  0, "Basic"),
	("Charging Port Replacement",       "Charging & Power",       "CHG-REP", 60,  1, "Intermediate"),
	("Charger / Adapter Replacement",   "Charging & Power",       "CHG-ADP", 10,  1, "Basic"),
	("Motherboard Diagnosis",           "Board Diagnosis",        "BRD-DIA", 120, 0, "Advanced"),
	("Board-Level Repair",              "Board Diagnosis",        "BRD-REP", 240, 1, "Expert"),
	("Thermal Service / Fan Cleaning",  "Board Diagnosis",        "BRD-THM", 60,  0, "Basic"),
	("Camera Replacement",              "Camera",                 "CAM-REP", 60,  1, "Intermediate"),
	("Camera Glass Replacement",        "Camera",                 "CAM-GLS", 30,  1, "Basic"),
	("Speaker Replacement",             "Speaker & Mic",          "AUD-SPK", 45,  1, "Intermediate"),
	("Microphone Replacement",          "Speaker & Mic",          "AUD-MIC", 45,  1, "Intermediate"),
	("Audio Cleaning & Diagnosis",      "Speaker & Mic",          "AUD-DIA", 20,  0, "Basic"),
	("Liquid Damage Treatment",         "Water Damage",           "WTR-TRT", 240, 0, "Advanced"),
	("Post-Liquid Component Replacement", "Water Damage",         "WTR-CMP", 120, 1, "Expert"),
	("Back Panel Replacement",          "Physical Damage",        "PHY-BCK", 45,  1, "Basic"),
	("Hinge Repair",                    "Physical Damage",        "PHY-HNG", 90,  1, "Intermediate"),
	("Strap Replacement",               "Physical Damage",        "PHY-STR", 10,  1, "Basic"),
	("Body / Frame Repair",             "Physical Damage",        "PHY-FRM", 120, 1, "Advanced"),
	("OS Reinstall / Update",           "Software",               "SFT-OSR", 60,  0, "Basic"),
	("Password / FRP Unlock",           "Software",               "SFT-FRP", 45,  0, "Intermediate"),
	("Virus Removal & Tune-up",         "Software",               "SFT-VIR", 60,  0, "Basic"),
	("Network Diagnosis",               "Network & Connectivity", "NET-DIA", 30,  0, "Basic"),
	("WiFi / Bluetooth Module Replacement", "Network & Connectivity", "NET-WBT", 90, 1, "Advanced"),
	("Antenna / Network IC Repair",     "Network & Connectivity", "NET-ANT", 120, 1, "Expert"),
	("Fingerprint Sensor Replacement",  "Sensors & Biometrics",   "SNS-FPR", 60,  1, "Advanced"),
	("Face ID / Sensor Diagnosis",      "Sensors & Biometrics",   "SNS-DIA", 45,  0, "Advanced"),
	("Button / Flex Replacement",       "Buttons & Keys",         "BTN-REP", 45,  1, "Intermediate"),
	("Keyboard Replacement",            "Buttons & Keys",         "BTN-KBD", 60,  1, "Intermediate"),
	("Touchpad Replacement",            "Buttons & Keys",         "BTN-TPD", 60,  1, "Intermediate"),
	("Data Backup & Transfer",          "Data Recovery",          "DAT-BAK", 60,  0, "Basic"),
	("Advanced Data Recovery",          "Data Recovery",          "DAT-REC", 480, 0, "Expert"),
	("Stylus / Accessory Pairing & Repair", "Accessories",        "ACC-STY", 30,  0, "Basic"),
	("Full Device Diagnosis",           "General Diagnosis",      "GEN-DIA", 45,  0, "Basic"),
]


# ---------------------------------------------------------------------------
# Walk-in sources & withdrawal reasons consumed by Service Request.
# ---------------------------------------------------------------------------

_WALKIN_SOURCES = [
	{"source_name": "Walk-in", "description": "Customer walked into our store directly"},
	{"source_name": "Website", "description": "Customer found us through our website"},
	{"source_name": "Phone Call", "description": "Customer called us"},
	{"source_name": "Referral", "description": "Referred by existing customer"},
	{"source_name": "Social Media", "description": "Found us on social media (Facebook, Instagram, etc.)"},
	{"source_name": "Google Search", "description": "Found us through Google search"},
	{"source_name": "Advertisement", "description": "Saw our advertisement"},
	{"source_name": "POS Counter", "description": "Walk-in logged from POS counter"},
]

# ``applies_to`` decides which closing outcome offers the reason. Leaving them
# all on "Any" would put "Parts Not Available" in front of a counter hand
# recording that the customer changed their mind — which is how a coded reason
# turns back into noise.
_WITHDRAWAL_REASONS = [
	{"reason_name": "Too Expensive", "reason_type": "Financial Constraint",
	 "applies_to": "Customer Declined", "description": "Customer found repair cost too high"},
	{"reason_name": "Fixed Elsewhere", "reason_type": "Customer Decision",
	 "applies_to": "Customer Cancelled", "description": "Customer got it repaired somewhere else"},
	{"reason_name": "Not Repairable", "reason_type": "Technical Limitation",
	 "applies_to": "Not Repairable", "description": "Device cannot be repaired"},
	{"reason_name": "Customer Changed Mind", "reason_type": "Customer Decision",
	 "applies_to": "Customer Cancelled", "description": "Customer decided not to proceed"},
	{"reason_name": "Buying New Device", "reason_type": "Customer Decision",
	 "applies_to": "Customer Cancelled", "description": "Customer decided to buy new device instead"},
	{"reason_name": "Parts Not Available", "reason_type": "Technical Limitation",
	 "applies_to": "Not Repairable", "description": "Required spare parts not available"},
	{"reason_name": "Takes Too Long", "reason_type": "Customer Decision",
	 "applies_to": "Customer Cancelled", "description": "Repair time too long for customer"},

	# The outcomes the list could not express at all. BER had no reason of its
	# own, so "not worth fixing" was being recorded as "cannot be fixed".
	{"reason_name": "Repair Cost Exceeds Device Value", "reason_type": "Financial Constraint",
	 "applies_to": "BER", "description": "Beyond economic repair — quote is worth more than the handset"},
	{"reason_name": "Water Damage Beyond Recovery", "reason_type": "Technical Limitation",
	 "applies_to": "Not Repairable", "description": "Corrosion or board damage past recovery"},
	{"reason_name": "Physical Damage Beyond Recovery", "reason_type": "Technical Limitation",
	 "applies_to": "Not Repairable", "description": "Housing or board damage past recovery"},
	{"reason_name": "No Fault Found", "reason_type": "Technical Limitation",
	 "applies_to": "Customer Cancelled", "description": "Reported fault could not be reproduced"},
	{"reason_name": "Estimate Not Approved In Time", "reason_type": "Customer Decision",
	 "applies_to": "Customer Declined", "description": "Estimate lapsed without a decision"},
	{"reason_name": "Other", "reason_type": "Customer Decision", "applies_to": "Any",
	 "requires_note": 1, "description": "Anything the list does not cover — say what happened"},
]


# ---------------------------------------------------------------------------
# QC templates: template -> (issue_category | None, [(check, mandatory, critical, type)])
# The template with no issue_category is the generic fallback
# _populate_qc_checklist uses when no category-specific template matches.
# ---------------------------------------------------------------------------

_QC_TEMPLATES = {
	"Standard Post-Repair QC": (None, [
		("Device powers on", 1, 1, "Pass-Fail"),
		("Reported issue resolved", 1, 1, "Pass-Fail"),
		("Screen and touch responsive", 1, 0, "Pass-Fail"),
		("Cameras working (front and rear)", 0, 0, "Pass-Fail"),
		("Speaker and microphone working", 0, 0, "Pass-Fail"),
		("SIM / network detected", 0, 0, "Pass-Fail"),
		("WiFi / Bluetooth working", 0, 0, "Pass-Fail"),
		("All buttons functional", 0, 0, "Pass-Fail"),
		("No new cosmetic damage", 1, 0, "Pass-Fail"),
		("IMEI / serial matches job card", 1, 1, "Pass-Fail"),
		("Customer data intact", 1, 1, "Pass-Fail"),
		("Device cleaned before handover", 0, 0, "Pass-Fail"),
	]),
	"Screen & Display QC": ("Screen & Display", [
		("Display uniform — no lines, spots or bleed", 1, 1, "Pass-Fail"),
		("Touch responsive across full screen", 1, 1, "Pass-Fail"),
		("Auto-brightness / true tone working", 0, 0, "Pass-Fail"),
		("Screen fitment flush — no gaps", 1, 0, "Pass-Fail"),
		("Face ID / proximity sensors working after screen change", 1, 1, "Pass-Fail"),
		("No dust under glass", 1, 0, "Pass-Fail"),
	]),
	"Battery QC": ("Battery", [
		("Battery health measured and recorded", 1, 1, "Measurement"),
		("Charge / discharge cycle tested", 1, 0, "Pass-Fail"),
		("No swelling or abnormal heating", 1, 1, "Pass-Fail"),
		("Battery seated and sealed correctly", 1, 0, "Pass-Fail"),
	]),
	"Water Damage QC": ("Water Damage", [
		("Corrosion cleaned and board dried", 1, 1, "Pass-Fail"),
		("All connectors reseated", 1, 0, "Pass-Fail"),
		("Battery health verified post-treatment", 1, 1, "Pass-Fail"),
		("Charging behaviour normal", 1, 1, "Pass-Fail"),
		("Liquid damage indicators photographed", 1, 0, "Photo"),
		("24-hour soak test completed", 1, 0, "Pass-Fail"),
	]),
	# Outgoing-inspection packs modelled on OEM service-centre QC
	# (Apple AST 2 diagnostics suite / Samsung GSPN final inspection).
	"Charging & Power QC": ("Charging & Power", [
		("Wired charging at rated wattage", 1, 1, "Measurement"),
		("Charging port seated — no debris or wobble", 1, 0, "Pass-Fail"),
		("Wireless charging working (if supported)", 0, 0, "Pass-Fail"),
		("No abnormal heat while charging", 1, 1, "Pass-Fail"),
		("Battery percentage rises consistently", 1, 0, "Pass-Fail"),
	]),
	"Board Diagnosis QC": ("Board Diagnosis", [
		("All power rails at spec voltage", 1, 1, "Measurement"),
		("Repaired area inspected under microscope", 1, 1, "Pass-Fail"),
		("No boot loop across 5 restarts", 1, 1, "Pass-Fail"),
		("Baseband / WiFi chips detected in diagnostics", 1, 1, "Pass-Fail"),
		("30-minute stress test — no thermal shutdown", 1, 0, "Pass-Fail"),
		("Board photos taken before reassembly", 1, 0, "Photo"),
	]),
	"Camera QC": ("Camera", [
		("Rear camera photo and video sample clear", 1, 1, "Pass-Fail"),
		("Front camera photo and video sample clear", 1, 1, "Pass-Fail"),
		("Autofocus / OIS working — no rattle", 1, 0, "Pass-Fail"),
		("No dust or fingerprints inside lens", 1, 0, "Pass-Fail"),
		("Flash fires and exposure normal", 0, 0, "Pass-Fail"),
		("Portrait / depth mode working (if supported)", 0, 0, "Pass-Fail"),
	]),
	"Speaker & Mic QC": ("Speaker & Mic", [
		("Loudspeaker clear at max volume — no distortion", 1, 1, "Pass-Fail"),
		("Earpiece clear on live call test", 1, 1, "Pass-Fail"),
		("Microphone recording clear (voice memo test)", 1, 1, "Pass-Fail"),
		("Noise-cancellation mic working on call", 0, 0, "Pass-Fail"),
		("Speaker mesh clean and sealed", 0, 0, "Pass-Fail"),
	]),
	"Physical Damage QC": ("Physical Damage", [
		("Frame and housing aligned — no gaps", 1, 1, "Pass-Fail"),
		("All screws present and torqued", 1, 0, "Pass-Fail"),
		("Buttons click and return correctly", 1, 0, "Pass-Fail"),
		("Water-seal adhesive reapplied", 1, 0, "Pass-Fail"),
		("Before / after photos on record", 1, 0, "Photo"),
	]),
	"Software QC": ("Software", [
		("Device boots to latest supported OS", 1, 1, "Pass-Fail"),
		("Built-in diagnostics suite passes", 1, 1, "Pass-Fail"),
		("No test profiles / developer artifacts left on device", 1, 1, "Pass-Fail"),
		("Activation and sign-in screens reachable", 1, 0, "Pass-Fail"),
		("Idle battery drain normal over 30 minutes", 0, 0, "Pass-Fail"),
	]),
	"Network & Connectivity QC": ("Network & Connectivity", [
		("SIM detected in all slots", 1, 1, "Pass-Fail"),
		("Live call completes both ways", 1, 1, "Pass-Fail"),
		("Mobile data browsing works", 1, 0, "Pass-Fail"),
		("WiFi connects on 2.4GHz and 5GHz", 1, 0, "Pass-Fail"),
		("Bluetooth pairs and streams audio", 0, 0, "Pass-Fail"),
		("GPS gets a location lock outdoors", 0, 0, "Pass-Fail"),
	]),
	"Sensors & Biometrics QC": ("Sensors & Biometrics", [
		("Fingerprint enrols and unlocks reliably", 1, 1, "Pass-Fail"),
		("Face unlock working (if supported)", 1, 1, "Pass-Fail"),
		("Proximity sensor blanks screen on call", 1, 0, "Pass-Fail"),
		("Gyro / accelerometer — rotation test", 1, 0, "Pass-Fail"),
		("Ambient light auto-brightness responds", 0, 0, "Pass-Fail"),
		("Compass calibrates correctly", 0, 0, "Pass-Fail"),
	]),
	"Buttons & Keys QC": ("Buttons & Keys", [
		("Power button tactile — no sticking", 1, 1, "Pass-Fail"),
		("Volume rocker both directions working", 1, 0, "Pass-Fail"),
		("Mute / action switch working (if present)", 0, 0, "Pass-Fail"),
		("No key ghosting on keyboard (laptops)", 0, 0, "Pass-Fail"),
	]),
	"Data Recovery QC": ("Data Recovery", [
		("Recovered data verified against customer list", 1, 1, "Pass-Fail"),
		("Data handed over on agreed medium", 1, 1, "Pass-Fail"),
		("No customer data retained on shop systems", 1, 1, "Pass-Fail"),
		("File integrity spot-checked (opens correctly)", 1, 0, "Pass-Fail"),
	]),
	"Accessories QC": ("Accessories", [
		("Accessory pairs / connects to device", 1, 1, "Pass-Fail"),
		("Genuine-part verification done", 1, 0, "Pass-Fail"),
		("Accessory physically undamaged and clean", 1, 0, "Pass-Fail"),
	]),
}


# ---------------------------------------------------------------------------
# Seeders
# ---------------------------------------------------------------------------


def _seed_grades() -> None:
	_migrate_legacy_grades()
	categories = frappe.get_all("Issue Category", pluck="name")
	for row in _GRADES:
		existing = frappe.db.exists("Technician Grade", row["grade_name"])
		if existing:
			# Keep seeded parents current, but only normalize child skill
			# matrices that still look like the old uniform seed. If ops has
			# tuned category-by-category skills, leave those choices intact.
			doc = frappe.get_doc("Technician Grade", existing)
			doc.grade_level = row["grade_level"]
			doc.description = row["description"]
			doc.is_active = 1
			have = {s.issue_category for s in doc.skills}
			missing = [c for c in categories if c not in have]
			if missing:
				for cat in missing:
					doc.append("skills", {"issue_category": cat, "max_skill_level": row["skill"]})
			current_levels = {s.max_skill_level for s in doc.skills if s.max_skill_level}
			if len(current_levels) <= 1 and current_levels != {row["skill"]}:
				for skill_row in doc.skills:
					skill_row.max_skill_level = row["skill"]
			doc.save(ignore_permissions=True)
			continue
		doc = frappe.new_doc("Technician Grade")
		doc.grade_name = row["grade_name"]
		doc.grade_level = row["grade_level"]
		doc.description = row["description"]
		doc.is_active = 1
		for cat in categories:
			doc.append("skills", {"issue_category": cat, "max_skill_level": row["skill"]})
		doc.insert(ignore_permissions=True)


def _migrate_legacy_grades() -> None:
	"""Move the original 3-level expert grade into the new L4 slot.

	The first service-master seed used ``L3 - Expert Technician`` as the
	top grade. L4 is now the expert tier, so existing links should keep
	their expert meaning instead of silently becoming only L3/advanced.
	"""
	for old_name, new_name in _LEGACY_GRADE_RENAMES.items():
		old_exists = frappe.db.exists("Technician Grade", old_name)
		if not old_exists:
			continue

		if not frappe.db.exists("Technician Grade", new_name):
			frappe.rename_doc(
				"Technician Grade",
				old_name,
				new_name,
				force=True,
				ignore_permissions=True,
				show_alert=False,
				rebuild_search=False,
			)
			continue

		frappe.rename_doc(
			"Technician Grade",
			old_name,
			new_name,
			force=True,
			merge=True,
			ignore_permissions=True,
			show_alert=False,
			rebuild_search=False,
		)


def _seed_solutions() -> None:
	from gofix.catalogue_sync import resolve_solution

	for name, category, code, minutes, requires_spare, skill in _SOLUTIONS:
		if not frappe.db.exists("Issue Category", category):
			continue
		# Repair Solution is keyed by solution_code. Resolve rather than assume,
		# so this seeder is idempotent on a site that has not yet been re-keyed
		# (docname == label) as well as on one that has (docname == code).
		_upsert(
			"Repair Solution",
			resolve_solution(code) or resolve_solution(name, category) or code,
			{
				"solution_name": name,
				"issue_category": category,
				"solution_code": code,
				"estimated_minutes": minutes,
				"requires_spare": requires_spare,
				"skill_level": skill,
				"minimum_grade": _GRADE_FOR_SKILL[skill],
				"is_active": 1,
			},
		)


def _seed_walkin_sources() -> None:
	for row in _WALKIN_SOURCES:
		values = dict(row)
		values["is_active"] = 1
		_upsert("Walkin Source", row["source_name"], values)


def _seed_withdrawal_reasons() -> None:
	for row in _WITHDRAWAL_REASONS:
		values = dict(row)
		values["is_active"] = 1
		_upsert("Withdrawal Reason", row["reason_name"], values)


def _seed_qc_templates() -> None:
	for template_name, (category, checks) in _QC_TEMPLATES.items():
		if category and not frappe.db.exists("Issue Category", category):
			continue
		existing = frappe.db.exists("GoFix QC Template", template_name)
		if existing:
			doc = frappe.get_doc("GoFix QC Template", existing)
			changed = False
			# QC packs are master data, not company data — a company value
			# (inherited from the seeding user's default) hides the template
			# from every other company's tickets. Heal to universal.
			if doc.company:
				doc.company = ""
				changed = True
			have = {c.check_name for c in doc.checks}
			new = [c for c in checks if c[0] not in have]
			for check_name, mandatory, critical, check_type in new:
				doc.append("checks", {
					"check_name": check_name,
					"is_mandatory": mandatory,
					"is_critical": critical,
					"check_type": check_type,
				})
				changed = True
			if changed:
				doc.save(ignore_permissions=True)
			continue
		doc = frappe.new_doc("GoFix QC Template")
		doc.template_name = template_name
		doc.issue_category = category
		doc.company = ""
		doc.is_active = 1
		for check_name, mandatory, critical, check_type in checks:
			doc.append("checks", {
				"check_name": check_name,
				"is_mandatory": mandatory,
				"is_critical": critical,
				"check_type": check_type,
			})
		doc.insert(ignore_permissions=True)
		if doc.company:
			# Company field may auto-default on insert — force universal.
			doc.db_set("company", "", update_modified=False)


def _seed_spare_category_mapping() -> None:
	"""Map each spares catalogue category to the device category it serves.

	Drives the category tier of spare applicability (a laptop spare never
	shows for a mobile repair). No-op until the custom field exists.
	"""

	if not frappe.db.has_column("CH Category", "gofix_spares_for_category"):
		return
	mapping = {
		"Mobile Spares": "Smart Phones",
		"Laptop Spares": "Laptops",
		"Tablet Spares": "Tablets",
		"Smart Watch Spares": "Watches",
	}
	for spares_cat, device_cat in mapping.items():
		if not (frappe.db.exists("CH Category", spares_cat) and frappe.db.exists("CH Category", device_cat)):
			continue
		if not frappe.db.get_value("CH Category", spares_cat, "gofix_spares_for_category"):
			frappe.db.set_value(
				"CH Category", spares_cat, "gofix_spares_for_category", device_cat, update_modified=False
			)


def _seed_service_order_state_machine() -> None:
	"""Mirror the active Service Order Workflow into the Service Order
	State/Transition masters.

	``CustomSalesOrder.validate_state_transition`` validates every
	workflow_state change against ``Service Order Transition`` rows — with the
	table empty, ALL real workflow saves throw "Invalid transition". Deriving
	the rows from the Workflow doc keeps the two definitions from drifting.
	"""

	if not (
		frappe.db.table_exists("Service Order State")
		and frappe.db.table_exists("Service Order Transition")
		and frappe.db.exists("Workflow", "Service Order Workflow")
	):
		return

	wf = frappe.get_doc("Workflow", "Service Order Workflow")

	for s in wf.states:
		if not frappe.db.exists("Service Order State", s.state):
			frappe.get_doc({"doctype": "Service Order State", "state_name": s.state}).insert(
				ignore_permissions=True
			)

	for t in wf.transitions:
		if not frappe.db.exists(
			"Service Order Transition", {"from_state": t.state, "to_state": t.next_state}
		):
			frappe.get_doc(
				{
					"doctype": "Service Order Transition",
					"from_state": t.state,
					"to_state": t.next_state,
					"action": t.action,
					"allowed_role": t.allowed,
				}
			).insert(ignore_permissions=True)


# ---------------------------------------------------------------------------
# Patch entrypoint
# ---------------------------------------------------------------------------


def execute() -> None:
	for doctype in (
		"Issue Category",
		"Repair Solution",
		"Technician Grade",
		"Walkin Source",
		"Withdrawal Reason",
		"GoFix QC Template",
		"GoFix SLA Rule",
		"GoFix Approval Rule",
	):
		if not frappe.db.table_exists(doctype):
			frappe.log_error(
				title="seed_gofix_service_masters",
				message=f"Table for {doctype} missing at patch time; skipping seed.",
			)
			return

	if not frappe.db.count("Issue Category"):
		# seed_gofix_token_masters must run first (patches.txt ordering).
		frappe.log_error(
			title="seed_gofix_service_masters",
			message="Issue Category is empty — token-masters seed did not run; skipping.",
		)
		return

	_seed_grades()
	_seed_solutions()
	_seed_walkin_sources()
	_seed_withdrawal_reasons()
	_seed_qc_templates()
	_seed_service_order_state_machine()
	_seed_spare_category_mapping()

	from gofix.setup.seed_default_rules import seed_approval_rules, seed_sla_rules

	seed_sla_rules()
	seed_approval_rules()

	frappe.db.commit()
	print(
		"Seeded GoFix service masters: "
		f"{len(_GRADES)} grades, {len(_SOLUTIONS)} repair solutions, "
		f"{len(_WALKIN_SOURCES)} walk-in sources, {len(_WITHDRAWAL_REASONS)} withdrawal reasons, "
		f"{len(_QC_TEMPLATES)} QC templates + SLA/approval rules."
	)
