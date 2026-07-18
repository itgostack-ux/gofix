import traceback

import frappe

SR = "SR-260718-10118"
SURESH, VIKRAM = "HR-EMP-00001", "HR-EMP-00002"


def jas(so):
	return frappe.get_all("Job Assignment", filters={"service_order": so, "docstatus": ("<", 2)},
		fields=["name", "service_engineer", "assignment_status", "actual_hours"], order_by="creation")


def open_rows():
	return frappe.get_all("GoFix Custody Log", filters={"service_request": SR, "released_at": ("is", "not set")},
		fields=["technician", "note"])


def run():
	sr = frappe.get_doc("Service Request", SR)
	so = sr.service_order

	# Heal: Vikram finished everything — release his stuck custody (committed)
	vik_ja = next((j for j in jas(so) if j.service_engineer == VIKRAM and j.assignment_status == "In Progress"), None)
	if vik_ja:
		doc = frappe.get_doc("Job Assignment", vik_ja.name)
		doc.assignment_status = "Completed"
		doc.end_datetime = doc.end_datetime or frappe.utils.now_datetime()
		doc.flags.ignore_validate_update_after_submit = True
		doc.save(ignore_permissions=True)
		print("X| healed: Vikram JA -> Completed (device released)")
	frappe.db.commit()

	frappe.db.commit = lambda *a, **k: None
	try:
		from gofix.gofix_services.page.gofix_ops_hub.gofix_ops_hub import (
			get_ticket_detail,
			handover_device,
			update_solution_status,
		)
		lines = {r.repair_solution: r for r in frappe.get_doc("Service Request", SR).solution_lines}
		virus, osl = lines["Virus Removal & Tune-up"], lines["OS Reinstall / Update"]

		# C1: Suresh can now start his solution (screenshot blocker gone)
		update_solution_status(SR, virus.name, "In Progress")
		holder = [j for j in jas(so) if j.assignment_status == "In Progress"]
		opened = open_rows()
		ok = holder and holder[0].service_engineer == SURESH and opened and opened[0].technician == SURESH
		print("X| C1", "PASS" if ok else "FAIL", "— Suresh started; holder:", holder and holder[0].service_engineer,
			"| custody row open:", bool(opened))

		# C2: hold -> custody row closes with hours, actual_hours accumulates
		update_solution_status(SR, virus.name, "On Hold", remarks="parts")
		closed = frappe.get_all("GoFix Custody Log", filters={"service_request": SR, "technician": SURESH,
			"released_at": ("is", "set")}, fields=["hours"])
		act = frappe.db.get_value("Job Assignment", {"service_order": so, "service_engineer": SURESH,
			"docstatus": ("<", 2)}, "actual_hours")
		print("X| C2", "PASS" if closed and not open_rows() else "FAIL",
			"— period closed, hours:", closed and closed[0].hours, "| JA actual_hours:", act)

		# C3: device handover Suresh -> Vikram (custody only; solutions stay)
		update_solution_status(SR, virus.name, "In Progress")
		handover_device(SR, VIKRAM, remarks="Vikram to verify board before Suresh continues")
		holder = [j for j in jas(so) if j.assignment_status == "In Progress"]
		opened = open_rows()
		still_suresh = frappe.db.get_value("SR Solution Line", virus.name, "technician")
		ok = holder and holder[0].service_engineer == VIKRAM and opened and opened[0].technician == VIKRAM \
			and "verify board" in (opened[0].note or "") and still_suresh == SURESH
		print("X| C3", "PASS" if ok else "FAIL", "— holder now:", holder and holder[0].service_engineer,
			"| note:", opened and opened[0].note, "| virus line still Suresh:", still_suresh == SURESH)

		# C4: handover to someone not on the ticket -> blocked
		try:
			handover_device(SR, "HR-EMP-00003", remarks="x")
			print("X| C4 FAIL — handover to outsider allowed")
		except Exception as e:
			print("X| C4 PASS —", str(e)[:60])

		# C5: Suresh finishes BOTH his solutions -> his JA auto-Completed
		handover_device(SR, SURESH, remarks="back to Suresh")
		update_solution_status(SR, virus.name, "Completed", remarks="cleaned")
		update_solution_status(SR, osl.name, "In Progress")
		update_solution_status(SR, osl.name, "Completed", remarks="reinstalled")
		s_ja = frappe.get_all("Job Assignment", filters={"service_order": so, "service_engineer": SURESH,
			"docstatus": ("<", 2)}, fields=["assignment_status", "actual_hours", "end_datetime"])
		done = [j for j in s_ja if j.assignment_status == "Completed"]
		print("X| C5", "PASS" if done and not open_rows() else "FAIL",
			"— Suresh JA:", [(j.assignment_status, j.actual_hours) for j in s_ja], "| open rows:", len(open_rows()))

		# C6: detail payload carries holder + custody history
		d = get_ticket_detail(SR)
		print("X| C6", "PASS" if "device_holder" in d and len(d["custody_log"]) >= 3 else "FAIL",
			"— holder:", d["device_holder"] or "(none)", "| log rows:", len(d["custody_log"]))
	except Exception:
		traceback.print_exc()
	frappe.db.rollback()
	print("X| rolled back — kept state:", [(j.service_engineer, j.assignment_status) for j in jas(so)],
		"| custody rows kept:", frappe.db.count("GoFix Custody Log", {"service_request": SR}))
