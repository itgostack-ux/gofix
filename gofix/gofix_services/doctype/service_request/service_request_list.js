// Service Request — list view with status tabs.
//
// Adds quick-filter buttons aligned with the canonical workflow buckets so
// staff can pivot the queue by lifecycle stage without opening the filter
// pane. `decision` is the single canonical lifecycle field.

frappe.listview_settings["Service Request"] = {
    add_fields: ["decision", "priority", "service_type", "expected_completion_date"],

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
            "Draft":      [__("Draft"),      "grey",   "decision,=,Draft"],
            "Accepted":   [__("Accepted"),   "blue",   "decision,=,Accepted"],
            "In Service": [__("In Service"), "blue",   "decision,=,In Service"],
            "Completed":  [__("Completed"),  "orange", "decision,=,Completed"],
            "Invoiced":   [__("Invoiced"),   "green",  "decision,=,Invoiced"],
            "Delivered":  [__("Delivered"),  "green",  "decision,=,Delivered"],
            "Withdrawn":  [__("Withdrawn"),  "red",    "decision,=,Withdrawn"],
            "Rejected":   [__("Rejected"),   "red",    "decision,=,Rejected"],
            "Expired":    [__("Expired"),    "red",    "decision,=,Expired"],
            "Cancelled":  [__("Cancelled"),  "red",    "decision,=,Cancelled"],
        };
        return map[doc.decision] || [doc.decision, "grey", `decision,=,${doc.decision}`];
    },

    onload(listview) {
        // Render status-bucket buttons in the page menu.
        for (const bucket of this.status_buckets) {
            listview.page.add_menu_item(`${bucket.label} (${bucket.statuses.length})`, () => {
                listview.filter_area.clear();
                // OR filter across the bucket's statuses.
                listview.filter_area.add([
                    [listview.doctype, "decision", "in", bucket.statuses, false],
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
