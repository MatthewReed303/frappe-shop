frappe.ui.form.on("Shop Trade Application", {
	refresh(frm) {
		if (frm.is_new() || frm.doc.status !== "Pending") return;
		frm.add_custom_button(__("Approve"), () => {
			frm.save().then(() =>
				frappe.call({
					method: "shop.storefront.accounts.approve_trade",
					args: { name: frm.doc.name },
					freeze: true,
					freeze_message: __("Approving"),
					callback: () => frm.reload_doc(),
				})
			);
		});
		frm.add_custom_button(__("Decline"), () => {
			frappe.prompt(
				{ fieldname: "reason", fieldtype: "Small Text", label: __("Reason (sent to the applicant)") },
				(values) =>
					frappe.call({
						method: "shop.storefront.accounts.decline_trade",
						args: { name: frm.doc.name, reason: values.reason },
						callback: () => frm.reload_doc(),
					}),
				__("Decline Application"),
				__("Decline")
			);
		});
	},
});
