// Quick setup tool for GoFix warehouses

frappe.ui.form.on('Company', {
    refresh: function(frm) {
        if (frm.doc.country === 'India') {
            frm.add_custom_button(__('Setup GoFix Warehouses'), function() {
                frappe.confirm(
                    __('This will create warehouse structure for multi-store operations. Continue?'),
                    function() {
                        frappe.call({
                            method: 'gofix.setup.warehouse_setup.setup_warehouses_for_company',
                            args: {
                                company: frm.doc.name
                            },
                            callback: function(r) {
                                frappe.show_alert({
                                    message: __('Warehouse setup completed'),
                                    indicator: 'green'
                                }, 5);
                                frm.reload_doc();
                            }
                        });
                    }
                );
            }, __('GoFix'));
        }
    }
});

frappe.ui.form.on('Warehouse', {
    refresh: function(frm) {
        // Add button to create address for warehouse
        if (!frm.is_new() && !frm.doc.is_group) {
            frm.add_custom_button(__('Create Address'), function() {
                let d = new frappe.ui.Dialog({
                    title: __('Create Store Address'),
                    fields: [
                        {
                            fieldname: 'address_line1',
                            label: __('Address Line 1'),
                            fieldtype: 'Data',
                            reqd: 1
                        },
                        {
                            fieldname: 'city',
                            label: __('City'),
                            fieldtype: 'Data',
                            reqd: 1
                        },
                        {
                            fieldname: 'state',
                            label: __('State'),
                            fieldtype: 'Link',
                            options: 'State',
                            reqd: 1
                        },
                        {
                            fieldname: 'pincode',
                            label: __('Pincode'),
                            fieldtype: 'Data',
                            reqd: 1
                        }
                    ],
                    primary_action_label: __('Create'),
                    primary_action(values) {
                        frappe.call({
                            method: 'gofix.setup.warehouse_setup.create_store_address',
                            args: {
                                warehouse: frm.doc.name,
                                address_line1: values.address_line1,
                                city: values.city,
                                state: values.state,
                                pincode: values.pincode
                            },
                            callback: function(r) {
                                if (r.message) {
                                    frm.reload_doc();
                                    d.hide();
                                }
                            }
                        });
                    }
                });
                d.show();
            }, __('Actions'));
        }
        
        // Show warehouse details
        if (frm.doc.address) {
            frm.add_custom_button(__('View Details'), function() {
                frappe.call({
                    method: 'gofix.setup.warehouse_setup.get_warehouse_details',
                    args: {
                        warehouse: frm.doc.name
                    },
                    callback: function(r) {
                        if (r.message) {
                            let details = r.message;
                            frappe.msgprint({
                                title: __('Warehouse Details'),
                                message: `
                                    <div class="row">
                                        <div class="col-md-6">
                                            <p><strong>Warehouse:</strong> ${details.warehouse_name}</p>
                                            <p><strong>Type:</strong> ${details.warehouse_type || 'N/A'}</p>
                                            <p><strong>Company:</strong> ${details.company}</p>
                                        </div>
                                        <div class="col-md-6">
                                            <p><strong>City:</strong> ${details.city || 'N/A'}</p>
                                            <p><strong>State:</strong> ${details.state_name || 'N/A'}</p>
                                            <p><strong>State Code:</strong> ${details.state_code || 'N/A'}</p>
                                            <p><strong>Pincode:</strong> ${details.pincode || 'N/A'}</p>
                                        </div>
                                    </div>
                                `,
                                indicator: 'blue'
                            });
                        }
                    }
                });
            }, __('Info'));
        }
    }
});

// User default warehouse setter
frappe.ui.form.on('User', {
    refresh: function(frm) {
        if (!frm.is_new() && frm.doc.enabled) {
            frm.add_custom_button(__('Set Default Warehouse'), function() {
                let d = new frappe.ui.Dialog({
                    title: __('Set Default Warehouse'),
                    fields: [
                        {
                            fieldname: 'warehouse',
                            label: __('Warehouse'),
                            fieldtype: 'Link',
                            options: 'Warehouse',
                            reqd: 1,
                            get_query: function() {
                                return {
                                    filters: {
                                        is_group: 0
                                    }
                                };
                            }
                        }
                    ],
                    primary_action_label: __('Set'),
                    primary_action(values) {
                        frappe.call({
                            method: 'gofix.setup.warehouse_setup.set_user_default_warehouse',
                            args: {
                                user: frm.doc.name,
                                warehouse: values.warehouse
                            },
                            callback: function(r) {
                                frappe.show_alert({
                                    message: __('Default warehouse set successfully'),
                                    indicator: 'green'
                                }, 3);
                                d.hide();
                            }
                        });
                    }
                });
                d.show();
            }, __('GoFix'));
        }
    }
});
