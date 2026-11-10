// Addresses suggested as Address Line 1 is typed, from the lookup service in Shop Settings.
frappe.ui.form.on("Address", {
	refresh(frm) {
		suggest_addresses(frm);
	},
});

function suggest_addresses(frm) {
	const input = frm.fields_dict.address_line1 && frm.fields_dict.address_line1.$input;
	if (!input || input.data("shop-address-lookup")) return;
	input.data("shop-address-lookup", true);

	const new_session = () => frappe.utils.get_random(20);
	let session = new_session();
	let suggestions = [];
	let enabled = true;
	let timer = null;
	let seq = 0;

	const awesomplete = new Awesomplete(input.get(0), {
		minChars: 0,
		maxItems: 8,
		autoFirst: true,
		sort: false,
		filter: () => true,
		replace: () => {},
		item: (item) =>
			$(`<li><div><strong>${frappe.utils.escape_html(item.label)}</strong>
				<div class="small text-muted">${frappe.utils.escape_html(item.value.detail || "")}</div></div></li>`).get(0),
		list: [],
	});

	input.on("input", () => {
		if (!enabled) return;
		clearTimeout(timer);
		const text = (input.val() || "").trim();
		if (text.length < 3) return;
		timer = setTimeout(async () => {
			const mine = ++seq;
			const result = await frappe.xcall("shop.storefront.address_lookup.suggest", {
				text,
				country: frm.doc.country,
				session,
			});
			if (!result.enabled) {
				enabled = false;
				return;
			}
			if (mine !== seq) return;
			suggestions = result.suggestions || [];
			awesomplete.list = suggestions.map((row, index) => ({ label: row.label, value: { index, detail: row.detail } }));
			awesomplete.evaluate();
		}, 250);
	});

	input.on("awesomplete-selectcomplete", async (event) => {
		const suggestion = suggestions[event.originalEvent.text.value.index];
		if (!suggestion) return;
		const address =
			suggestion.address ||
			(await frappe.xcall("shop.storefront.address_lookup.address", { id: suggestion.id, session }));
		session = new_session();
		const values = {
			address_line1: address.address_line1,
			city: address.city,
			state: address.state,
			pincode: address.pincode,
		};
		if (address.address_line2) values.address_line2 = address.address_line2;
		if (address.country) values.country = address.country;
		await frm.set_value(values);
	});
}
