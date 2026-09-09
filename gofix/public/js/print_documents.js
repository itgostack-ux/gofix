/**
 * The two documents a repair produces, printed the same way everywhere.
 *
 * A counter should not have to know which screen they are on to print a job
 * sheet, and should never be offered an invoice for a repair nobody has billed.
 * The server decides which of the two exist for a ticket; this just draws the
 * buttons and opens the print view.
 */
window.gofix_print_documents = {
	/** Ask the server what this repair can produce. */
	fetch(service_request) {
		return frappe.xcall('gofix.report_filters.printable_documents',
			{ service_request });
	},

	/** Open one of them. */
	open(entry) {
		if (!entry || !entry.available) return;
		const qs = new URLSearchParams({
			doctype: entry.doctype,
			name: entry.name,
			format: entry.format,
			no_letterhead: '0',
			_lang: (frappe.boot && frappe.boot.lang) || 'en',
		});
		window.open(`/printview?${qs.toString()}`, '_blank');
	},

	/**
	 * Buttons for both documents. The invoice is shown disabled with its reason
	 * rather than hidden: "why can I not print the bill?" is a question the
	 * counter asks out loud, and a missing button does not answer it.
	 */
	buttons_html(docs, opts) {
		const o = opts || {};
		const cls = o.btn_class || 'btn btn-xs btn-default';
		const esc = frappe.utils.escape_html;
		const inv = docs.invoice || {};
		return `
			<button class="${cls} gofix-print-doc" data-doc="job_sheet">
				<i class="fa fa-file-text-o"></i> ${__('Job Sheet')}
			</button>
			<button class="${cls} gofix-print-doc" data-doc="invoice"
				${inv.available ? '' : 'disabled'}
				title="${esc(inv.available ? __('Print the invoice') : (inv.reason || ''))}">
				<i class="fa fa-print"></i> ${__('Invoice')}
			</button>`;
	},

	/** Wire those buttons inside a container. */
	bind($root, docs) {
		$root.off('click.gofixPrint').on('click.gofixPrint', '.gofix-print-doc', (e) => {
			const which = $(e.currentTarget).data('doc');
			this.open(docs[which]);
		});
	},
};
