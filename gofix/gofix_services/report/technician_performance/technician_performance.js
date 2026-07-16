// Copyright (c) 2026, GoStack and contributors
// For license information, please see license.txt

const technician_performance_active_company = () => {
	const lock = window.ch_erp15 && window.ch_erp15.company_lock;
	if (lock && typeof lock.active_company === "function") {
		return lock.active_company() || "";
	}
	return frappe.defaults.get_user_default("Company") || frappe.defaults.get_user_default("company") || "";
};

frappe.query_reports["Technician Performance"] = {
	filters: [
		{
			fieldname: "company",
			label: __("Company"),
			fieldtype: "Link",
			options: "Company",
			default: technician_performance_active_company(),
		},
		{
			fieldname: "from_date",
			label: __("From Date"),
			fieldtype: "Date",
			default: frappe.datetime.month_start(),
		},
		{
			fieldname: "to_date",
			label: __("To Date"),
			fieldtype: "Date",
			default: frappe.datetime.now_date(),
		}
	],
};
