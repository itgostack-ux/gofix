// Copyright (c) 2026, GoStack and contributors
// For license information, please see license.txt

const gofix_daily_tokens_active_company = () => {
	const lock = window.ch_erp15 && window.ch_erp15.company_lock;
	if (lock && typeof lock.active_company === "function") {
		return lock.active_company() || "";
	}
	return frappe.defaults.get_user_default("Company") || frappe.defaults.get_user_default("company") || "";
};

const gofix_daily_tokens_store_query = () => ({
	query: "gofix.gofix_services.store_context.warehouse_query",
	filters: {
		company: frappe.query_report.get_filter_value("company") || gofix_daily_tokens_active_company(),
	},
});

frappe.query_reports["GoFix Daily Tokens"] = {
	filters: [
		{
			fieldname: "company",
			label: __("Company"),
			fieldtype: "Link",
			options: "Company",
			default: gofix_daily_tokens_active_company(),
		},
		{
			fieldname: "store",
			label: __("Store"),
			fieldtype: "Link",
			options: "Warehouse",
			get_query: gofix_daily_tokens_store_query,
		},
		{
			fieldname: "from_date",
			label: __("From Date"),
			fieldtype: "Date",
			default: frappe.datetime.add_days(frappe.datetime.get_today(), -29),
		},
		{
			fieldname: "to_date",
			label: __("To Date"),
			fieldtype: "Date",
			default: frappe.datetime.get_today(),
		},
	],
};
