// Custom List View for Service Orders
// This filters Sales Order list to show only Service Orders (is_service_order = 1)

frappe.listview_settings['Sales Order'] = {
	...frappe.listview_settings['Sales Order'],
	
	// Add custom filter button for Service Orders
	onload: function(listview) {
		// Add "Service Orders Only" filter button
		if (listview.page) {
			listview.page.add_inner_button(__('Service Orders Only'), function() {
				listview.filter_area.clear();
				listview.filter_area.add([[listview.doctype, 'is_service_order', '=', 1]]);
			});
			
			// Add "Regular Sales Orders" filter button  
			listview.page.add_inner_button(__('Regular Sales Orders'), function() {
				listview.filter_area.clear();
				listview.filter_area.add([
					[listview.doctype, 'is_service_order', '!=', 1]
				]);
			});
			
			// Add "All Orders" filter button
			listview.page.add_inner_button(__('All Orders'), function() {
				listview.filter_area.clear();
			});
		}
	},
	
	// Add indicators for Service Orders
	get_indicator: function(doc) {
		if (doc.is_service_order) {
			// Service Order indicators
			if (doc.status === "Draft") {
				return [__("Draft"), "gray", "status,=,Draft"];
			} else if (doc.status === "To Deliver and Bill") {
				return [__("Service Order"), "orange", "status,=,To Deliver and Bill"];
			} else if (doc.status === "To Bill") {
				return [__("Ready to Bill"), "blue", "status,=,To Bill"];
			} else if (doc.status === "To Deliver") {
				return [__("Ready to Deliver"), "purple", "status,=,To Deliver"];
			} else if (doc.status === "Completed") {
				return [__("Completed"), "green", "status,=,Completed"];
			} else if (doc.status === "Cancelled") {
				return [__("Cancelled"), "red", "status,=,Cancelled"];
			}
		} else {
			// Regular Sales Order indicators (default)
			if (doc.status === "Draft") {
				return [__("Draft"), "gray", "status,=,Draft"];
			} else if (doc.status === "On Hold") {
				return [__("On Hold"), "orange", "status,=,On Hold"];
			} else if (doc.status === "To Deliver and Bill") {
				return [__("To Deliver and Bill"), "yellow", "status,=,To Deliver and Bill"];
			} else if (doc.status === "To Bill") {
				return [__("To Bill"), "blue", "status,=,To Bill"];
			} else if (doc.status === "To Deliver") {
				return [__("To Deliver"), "purple", "status,=,To Deliver"];
			} else if (doc.status === "Completed") {
				return [__("Completed"), "green", "status,=,Completed"];
			} else if (doc.status === "Cancelled") {
				return [__("Cancelled"), "red", "status,=,Cancelled"];
			}
		}
	},
	
	// Format list row to highlight Service Orders
	formatters: {
		name: function(value, df, doc) {
			if (doc.is_service_order && doc.service_request) {
				let esc_val = frappe.utils.escape_html(value);
				let esc_sr = frappe.utils.escape_html(doc.service_request);
				return `<span class="badge" style="background-color: #4CAF50; color: white;">SERVICE</span> ${esc_val} <small>(SR: ${esc_sr})</small>`;
			}
			return frappe.utils.escape_html(value);
		}
	}
};
