import frappe


def run():
	frappe.reload_doc("gofix_services", "doctype", "gofix_custody_log")
	print("X| doctype:", bool(frappe.db.exists("DocType", "GoFix Custody Log")))
	rows = frappe.db.sql("""select sl.parent, count(*) c from `tabSR Solution Line` sl
		where sl.repair_solution = 'Battery Calibration Test' group by sl.parent""", as_dict=True)
	print("X| candidate SRs:", rows)
	for r in rows:
		lines = frappe.get_all("SR Solution Line", filters={"parent": r.parent},
			fields=["repair_solution", "status", "technician"])
		jas = frappe.get_all("Job Assignment", filters={"service_request": r.parent, "docstatus": ("<", 2)},
			fields=["name", "service_engineer", "assignment_status", "estimated_hours", "actual_hours"])
		print("X|", r.parent, [(l.repair_solution[:18], l.status, l.technician) for l in lines])
		print("X|   JAs:", [(j.name[-10:], j.service_engineer, j.assignment_status, j.actual_hours) for j in jas])
	frappe.db.commit()
