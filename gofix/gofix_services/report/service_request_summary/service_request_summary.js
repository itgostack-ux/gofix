// Copyright (c) 2026, GoStack and contributors
// For license information, please see license.txt

const service_request_summary_active_company = () => {
	const lock = window.ch_erp15 && window.ch_erp15.company_lock;
	if (lock && typeof lock.active_company === "function") {
		return lock.active_company() || "";
	}
	return frappe.defaults.get_user_default("Company") || frappe.defaults.get_user_default("company") || "";
};

const service_request_summary_store_query = () => ({
	query: "gofix.gofix_services.store_context.warehouse_query",
	filters: {
		company: frappe.query_report.get_filter_value("company") || service_request_summary_active_company(),
	},
});

frappe.query_reports["Service Request Summary"] = {
	filters: [
		{
			fieldname: "company",
			label: __("Company"),
			fieldtype: "Link",
			options: "Company",
			default: service_request_summary_active_company(),
		},
		{
			fieldname: "from_date",
			label: __("From Date"),
			fieldtype: "Date",
			default: frappe.datetime.month_start(),
		},
		{
			fieldname: "source_warehouse",
			label: __("Source Warehouse"),
			fieldtype: "Link",
			options: "Warehouse",
			get_query: service_request_summary_store_query,
		},
		{
			fieldname: "to_date",
			label: __("To Date"),
			fieldtype: "Date",
			default: frappe.datetime.now_date(),
		}
	],
};
