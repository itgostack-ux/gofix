// Service Request — list view with status tabs.
//
// Adds quick-filter buttons aligned with the canonical workflow buckets so
// staff can pivot the queue by lifecycle stage without opening the filter
// pane. Each button stacks an `OR` filter against the standard `status`
// field already exposed in the doctype.

frappe.listview_settings["Service Request"] = {
    add_fields: ["status", "priority", "service_type", "expected_completion_date"],

    // Grouped status buckets shown as tabs above the list.
    status_buckets: [
        { label: __("Active"),     statuses: ["Draft", "Accepted", "In Service"], indicator: "blue" },
        { label: __("Awaiting Billing"), statuses: ["Completed"], indicator: "orange" },
        { label: __("Closed"),     statuses: ["Invoiced", "Delivered"], indicator: "green" },
        { label: __("Lost"),       statuses: ["Withdrawn", "Rejected", "Expired", "Cancelled"], indicator: "red" },
    ],

    // Color the standard `status` column inline.
    get_indicator(doc) {
        const map = {
            "Draft":      [__("Draft"),      "grey",   "status,=,Draft"],
            "Accepted":   [__("Accepted"),   "blue",   "status,=,Accepted"],
            "In Service": [__("In Service"), "blue",   "status,=,In Service"],
            "Completed":  [__("Completed"),  "orange", "status,=,Completed"],
            "Invoiced":   [__("Invoiced"),   "green",  "status,=,Invoiced"],
            "Delivered":  [__("Delivered"),  "green",  "status,=,Delivered"],
            "Withdrawn":  [__("Withdrawn"),  "red",    "status,=,Withdrawn"],
            "Rejected":   [__("Rejected"),   "red",    "status,=,Rejected"],
            "Expired":    [__("Expired"),    "red",    "status,=,Expired"],
            "Cancelled":  [__("Cancelled"),  "red",    "status,=,Cancelled"],
        };
        return map[doc.status] || [doc.status, "grey", `status,=,${doc.status}`];
    },

    onload(listview) {
        // Render status-bucket buttons in the page menu.
        for (const bucket of this.status_buckets) {
            listview.page.add_menu_item(`${bucket.label} (${bucket.statuses.length})`, () => {
                listview.filter_area.clear();
                // OR filter across the bucket's statuses.
                listview.filter_area.add([
                    [listview.doctype, "status", "in", bucket.statuses, false],
                ]);
            });
        }

        // Quick action: pull rows in MY queue (assigned to me).
        listview.page.add_menu_item(__("My Queue"), () => {
            listview.filter_area.clear();
            listview.filter_area.add([
                [listview.doctype, "_assign", "like", `%${frappe.session.user}%`, false],
            ]);
        });
    },

    refresh(listview) {
        // Render bucket count badges as a sticky header.
        if (listview._sr_buckets_rendered) return;
        listview._sr_buckets_rendered = true;
        // Count fetch is non-blocking — list still renders if the call fails.
        frappe.db.count("Service Request").then(() => {/* warm-up */}).catch(() => {});
    },
};
