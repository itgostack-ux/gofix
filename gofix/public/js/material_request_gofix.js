// Gap 14: GoFix spare-parts MR → PO auto-conversion button
frappe.ui.form.on("Material Request", {
    refresh(frm) {
        if (
            frm.doc.docstatus === 1 &&
            frm.doc.material_request_type === "Purchase" &&
            frm.doc.custom_source === "GoFix"
        ) {
            frm.add_custom_button(__("Create Purchase Orders"), () => {
                frappe.confirm(
                    __("Create Purchase Orders from this Material Request grouped by supplier?"),
                    () => {
                        frappe.call({
                            method: "gofix.purchase_api.create_pos_from_material_request",
                            args: { material_request: frm.doc.name },
                            freeze: true,
                            freeze_message: __("Creating Purchase Orders..."),
                            callback(r) {
                                const res = r.message || {};
                                const created = res.created || [];
                                if (created.length) {
                                    frappe.msgprint({
                                        title: __("Purchase Orders Created"),
                                        message: __(
                                            "{0} PO(s) created: {1}",
                                            [created.length, created.map(n =>
                                                `<a href='/app/purchase-order/${n}'>${n}</a>`
                                            ).join(", ")]
                                        ),
                                        indicator: "green",
                                    });
                                    frm.reload_doc();
                                }
                                if (res.warning) {
                                    frappe.msgprint({
                                        title: __("Missing Supplier"),
                                        message: res.warning,
                                        indicator: "orange",
                                    });
                                }
                            },
                        });
                    }
                );
            }, __("GoFix"));
        }
    },
});
