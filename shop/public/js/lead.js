// Trade account applications from the store are Leads with UTM Source "Store Trade Application".
frappe.ui.form.on("Lead", {
	refresh(frm) {
		const from_store = frm.doc.utm_source === "Store Trade Application";
		if (frm.is_new() || !from_store || frm.doc.status === "Converted" || frm.doc.qualification_status === "Unqualified") return;
		frm.add_custom_button(__("Approve Trade Account"), () => approve(frm), __("Trade Account"));
		frm.add_custom_button(__("Decline"), () => decline(frm), __("Trade Account"));
	},
});

async function approve(frm) {
	const settings = await frappe.db.get_value("Shop Settings", "Shop Settings", "trade_customer_group");
	const group = settings.message && settings.message.trade_customer_group;
	const terms = group ? (await frappe.db.get_value("Customer Group", group, "payment_terms")).message : {};
	const dialog = new frappe.ui.Dialog({
		title: __("Approve Trade Account for {0}", [frm.doc.company_name || frm.doc.lead_name]),
		fields: [
			{ fieldname: "customer_group", fieldtype: "Link", options: "Customer Group", label: __("Customer Group"), reqd: 1, default: group },
			{ fieldname: "payment_terms", fieldtype: "Link", options: "Payment Terms Template", label: __("Payment Terms"), default: terms && terms.payment_terms },
			{ fieldname: "allow_on_account", fieldtype: "Check", label: __("Pay on Account at the Store") },
			{ fieldname: "credit_limit", fieldtype: "Currency", label: __("Credit Limit") },
			{ fieldname: "price_list", fieldtype: "Link", options: "Price List", label: __("Price List"),
			  description: __("Optional. Usually leave empty: the Customer Group's Price List and Store Discounts apply.") },
		],
		primary_action_label: __("Approve"),
		primary_action(values) {
			frappe.call({
				method: "shop.storefront.accounts.approve_trade",
				args: { lead: frm.doc.name, ...values },
				freeze: true,
				freeze_message: __("Approving"),
				callback: (r) => {
					dialog.hide();
					frm.reload_doc();
					frappe.show_alert({ message: __("Customer {0} created", [r.message.customer]), indicator: "green" });
				},
			});
		},
	});
	dialog.show();
}

function decline(frm) {
	frappe.prompt(
		{ fieldname: "reason", fieldtype: "Small Text", label: __("Reason (sent to the applicant)") },
		(values) =>
			frappe.call({
				method: "shop.storefront.accounts.decline_trade",
				args: { lead: frm.doc.name, reason: values.reason },
				callback: () => frm.reload_doc(),
			}),
		__("Decline Application"),
		__("Decline")
	);
}
