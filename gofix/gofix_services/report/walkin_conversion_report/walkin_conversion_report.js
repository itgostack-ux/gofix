// Copyright (c) 2026, GoStack and contributors
// For license information, please see license.txt

const walkin_conversion_active_company = () => {
	const lock = window.ch_erp15 && window.ch_erp15.company_lock;
	if (lock && typeof lock.active_company === "function") {
		return lock.active_company() || "";
	}
	return frappe.defaults.get_user_default("Company") || frappe.defaults.get_user_default("company") || "";
};

const walkin_conversion_company = () => (
	frappe.query_report.get_filter_value("company") || walkin_conversion_active_company()
);

frappe.query_reports["Walk-in Conversion Report"] = {
	filters: [
		{
			fieldname: "company",
			label: __("Company"),
			fieldtype: "Link",
			options: "Company",
			default: walkin_conversion_active_company(),
		},
		{
			fieldname: "from_date",
			label: __("From Date"),
			fieldtype: "Date",
			reqd: 1,
			default: frappe.datetime.month_start(),
		},
		{
			fieldname: "to_date",
			label: __("To Date"),
			fieldtype: "Date",
			reqd: 1,
			default: frappe.datetime.get_today(),
		},
		{
			fieldname: "zone",
			label: __("Zone"),
			fieldtype: "Link",
			options: "CH Store Zone",
			get_query: () => ({ filters: { company: walkin_conversion_company() } }),
		},
		{
			fieldname: "city",
			label: __("City"),
			fieldtype: "Link",
			options: "CH City",
			get_query: () => ({
				query: "ch_erp15.ch_erp15.scope.hub_city_query",
				filters: { company: walkin_conversion_company() },
			}),
		},
		{
			fieldname: "store",
			label: __("Store"),
			fieldtype: "Link",
			options: "Warehouse",
			get_query: () => ({
				query: "gofix.gofix_services.store_context.warehouse_query",
				filters: { company: walkin_conversion_company() },
			}),
		},
	],
};
