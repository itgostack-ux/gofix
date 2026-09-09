// Copyright (c) 2026, GoStack and contributors
// For license information, please see license.txt

const device_history_active_company = () => {
	const lock = window.ch_erp15 && window.ch_erp15.company_lock;
	if (lock && typeof lock.active_company === "function") {
		return lock.active_company() || "";
	}
	return frappe.defaults.get_user_default("Company") || frappe.defaults.get_user_default("company") || "";
};

frappe.query_reports["Device Service History"] = {
	filters: [
		...gofix_scope_filters(),
		{
			fieldname: "customer",
			label: __("Customer"),
			fieldtype: "Link",
			options: "Customer",
		},
		{
			fieldname: "device_item",
			label: __("Device Item"),
			fieldtype: "Data",
		},
		{
			fieldname: "from_date",
			label: __("From Date"),
			fieldtype: "Date",
			default: frappe.datetime.month_start(),
		},
		{
			fieldname: "serial_no",
			label: __("Serial No"),
			fieldtype: "Data",
		},
		{
			fieldname: "to_date",
			label: __("To Date"),
			fieldtype: "Date",
			default: frappe.datetime.now_date(),
		}
	],
};
