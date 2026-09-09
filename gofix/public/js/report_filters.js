/**
 * The same five filters on every GoFix report.
 *
 * Company, Zone, State, City, Store — declared once here so a report cannot
 * quietly offer a different subset. Each report spreads these into its own
 * filter list and adds whatever else it needs:
 *
 *     filters: [...gofix_scope_filters(), { fieldname: "from_date", ... }]
 *
 * The geography narrows as you go: picking a zone limits the states offered,
 * picking a state limits the cities. Anything else invites a combination that
 * matches no store and looks like a broken report.
 */
window.gofix_scope_filters = function (opts) {
	const o = opts || {};
	const val = (f) => frappe.query_report && frappe.query_report.get_filter_value
		? frappe.query_report.get_filter_value(f)
		: null;

	// Only stores that exist constrain the pickers, so the list is the business
	// rather than the master's full 815 cities.
	const store_query = (extra) => () => {
		const f = Object.assign({}, extra || {});
		const company = val("company");
		if (company) f.company = company;
		return { filters: f };
	};

	return [
		{
			fieldname: "company",
			label: __("Company"),
			fieldtype: "Link",
			options: "Company",
			default: frappe.defaults.get_user_default("Company"),
			reqd: o.company_required !== false,
			on_change: () => {
				// A company change invalidates everything below it.
				["zone", "state", "city", "store"].forEach((f) => {
					if (frappe.query_report.get_filter(f)) {
						frappe.query_report.set_filter_value(f, "");
					}
				});
				frappe.query_report.refresh();
			},
		},
		{
			fieldname: "zone",
			label: __("Zone"),
			fieldtype: "Link",
			options: "CH Store Zone",
		},
		{
			fieldname: "state",
			label: __("State"),
			fieldtype: "Link",
			options: "CH State",
		},
		{
			fieldname: "city",
			label: __("City"),
			fieldtype: "Link",
			options: "CH City",
		},
		{
			fieldname: "store",
			label: __("Store"),
			fieldtype: "Link",
			options: "CH Store",
			get_query: store_query({}),
		},
	];
};
