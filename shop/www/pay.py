"""Card payment page: /pay/<order>?token=<cart token>. See shop.payments.stripe_checkout."""

import frappe
from frappe import _

from shop.payments import stripe_checkout
from shop.storefront import pricing

no_cache = 1
sitemap = 0


def get_context(context):
	form = frappe.form_dict
	order_name = form.get("order_id")
	token = form.get("token")
	try:
		from shop.storefront.checkout import elevated

		with elevated():
			order = stripe_checkout.load_order(order_name, token)
			settings = stripe_checkout.gateway_settings()
			paid = stripe_checkout.is_paid(order)
			due = stripe_checkout.amount_due(order)
	except frappe.PermissionError:
		frappe.local.flags.redirect_location = "/"
		raise frappe.Redirect
	if paid:
		frappe.local.flags.redirect_location = stripe_checkout.confirmation(order, token)
		raise frappe.Redirect
	store = frappe.get_cached_doc("Shop Settings")
	context.no_cache = 1
	context.title = _("Pay for order {0}").format(order.name)
	context.store_name = store.store_name or frappe.db.get_single_value("Website Settings", "app_name") or ""
	context.store_logo = store.store_logo
	context.order_name = order.name
	context.token = token
	context.currency = order.currency
	context.formatted_due = frappe.utils.fmt_money(due, currency=order.currency)
	context.lines = [
		{"label": f"{row.item_name} × {frappe.utils.cint(row.qty)}" if row.qty != 1 else row.item_name,
		 "amount": frappe.utils.fmt_money(row.amount, currency=order.currency)}
		for row in order.items
	]
	context.tax_note = tax_note(order)
	context.email = order.contact_email or ""
	context.publishable_key = settings.publishable_key if settings else ""
	context.ready = bool(settings and settings.publishable_key and settings.secret_key)
	context.back_url = stripe_checkout.confirmation(order, token)


def tax_note(order) -> str:
	included = sum(
		frappe.utils.flt(row.tax_amount_after_discount_amount)
		for row in order.taxes
		if row.included_in_print_rate
	)
	if not included:
		return ""
	return _("Includes {0} GST").format(frappe.utils.fmt_money(included, currency=order.currency))
