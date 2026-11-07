"""Quotes: a shopper asks for a quote instead of ordering, staff finish and send it from the desk,
and the customer accepts it online, which places the order.

request_quote  checkout's "Request a quote": a draft Quotation at the shopper's own prices
send_quote     desk button on a submitted Quotation: emails the customer a private link
quote_summary  the quote page (/quote/<name>?token=...)
accept_quote   the customer accepts: a Sales Order is made from the quote and paid like a checkout
"""

import json

import frappe
from frappe import _
from frappe.rate_limiter import rate_limit
from frappe.utils import add_days, cint, flt, getdate, nowdate

from shop.routes import store_url
from shop.storefront import cart as cart_module
from shop.storefront import pricing, totals

VALID_DAYS = 30


@frappe.whitelist(allow_guest=True, methods=["POST"])
@rate_limit(limit=10, seconds=60 * 10)
def request_quote(customer: dict, address: dict, shipping_option: str | None = None, message: str | None = None) -> dict:
	from shop.storefront.accounts import staff_emails
	from shop.storefront.checkout import create_address, elevated, get_or_create_customer

	if not frappe.get_cached_doc("Shop Settings").get("allow_quote_requests"):
		frappe.throw(_("This store doesn't take quote requests"))
	cart = cart_module.resolve_cart()
	if not cart or not cart.items:
		frappe.throw(_("Your cart is empty"))
	frappe.utils.validate_email_address(customer.get("email"), throw=True)
	if not customer.get("full_name"):
		frappe.throw(_("Name is required"))
	with elevated():
		order_quote = totals.compute(cart, address, shipping_option, "bank", totals.company_currency())
		party = get_or_create_customer(customer)
		shipping_address = create_address(party, customer, order_quote.address or address)
		settings = frappe.get_cached_doc("Shop Settings")
		quotation = frappe.get_doc(
			{
				"doctype": "Quotation",
				"quotation_to": "Customer",
				"party_name": party,
				"company": settings.company,
				"transaction_date": nowdate(),
				"valid_till": add_days(nowdate(), VALID_DAYS),
				"order_type": "Sales",
				"currency": order_quote.currency,
				"conversion_rate": 1,
				"selling_price_list": pricing.customer_price_list(party),
				"plc_conversion_rate": 1,
				"ignore_pricing_rule": 1,
				"customer_address": shipping_address.name,
				"shipping_address_name": shipping_address.name,
				"contact_email": customer["email"].strip().lower(),
				"items": [{"item_code": row.item_code, "qty": row.qty, "rate": row.rate} for row in order_quote.order_items],
			}
		)
		if order_quote.shipping and order_quote.shipping.customer_amount and settings.get("shipping_item"):
			quotation.append(
				"items",
				{"item_code": settings.shipping_item, "qty": 1, "rate": order_quote.shipping.customer_amount,
				 "description": order_quote.shipping["label"]},
			)
		if order_quote.tax_template:
			from shop.compat import get_sales_taxes

			quotation.taxes_and_charges = order_quote.tax_template
			for tax in get_sales_taxes(order_quote.tax_template):
				quotation.append("taxes", tax)
		quotation.shop_access_token = frappe.generate_hash(length=32)
		quotation.shop_request_message = (message or "").strip()[:2000]
		from shop.storefront import terms

		terms.apply_to(quotation)
		if order_quote.shipping:
			quotation.shop_shipping_option = json.dumps(
				{k: order_quote.shipping.get(k) for k in ("id", "label", "amount", "carrier", "service", "meta")}, default=str
			)
		quotation.flags.ignore_permissions = True
		quotation.insert(ignore_permissions=True)
		from shop.storefront import emails

		emails.send("quote_request_received", customer["email"], quotation)
		emails.send(
			"quote_request_staff", staff_emails(), quotation, customer_name=customer["full_name"],
			email=customer["email"], message=message or "", desk_url=frappe.utils.get_url("/desk/quotation/" + quotation.name),
		)
	return {"quote": quotation.name, "message": _("Thanks. We'll email your quote.")}


def quote_url(name: str, token: str) -> str:
	return frappe.utils.get_url(store_url(f"quote/{name}?token={token}"))


@frappe.whitelist(methods=["POST"])
def send_quote(name: str) -> str:
	"""Email the customer a link to view and accept a submitted quotation."""
	frappe.only_for(("System Manager", "Sales Manager", "Sales User", "Shop Manager"))
	quotation = frappe.get_doc("Quotation", name)
	if quotation.docstatus != 1:
		frappe.throw(_("Submit the quotation first"))
	email = quotation.contact_email
	if not email:
		frappe.throw(_("The quotation has no contact email"))
	if not quotation.get("shop_access_token"):
		quotation.db_set("shop_access_token", frappe.generate_hash(length=32))
	from shop.storefront import emails

	link = quote_url(quotation.name, quotation.shop_access_token)
	emails.send("quote_ready", email, quotation, quote_url=link, valid_till=frappe.utils.formatdate(quotation.valid_till))
	quotation.add_comment("Info", _("Quote emailed to {0}.").format(email))
	return link


def load_quote(name: str, token: str | None):
	if not name:
		raise frappe.PermissionError
	quotation = frappe.get_doc("Quotation", name)
	from shop.storefront.orders import session_customers

	if token and token == quotation.get("shop_access_token"):
		return quotation
	if quotation.party_name in session_customers():
		return quotation
	raise frappe.PermissionError


def quote_summary(name: str, token: str | None) -> dict:
	from shop.storefront.checkout import elevated

	with elevated():
		quotation = load_quote(name, token)
		order = frappe.db.get_value(
			"Sales Order Item", {"prevdoc_docname": quotation.name, "docstatus": 1}, "parent"
		)
	esc = frappe.utils.escape_html

	def fmt(amount):
		return frappe.utils.fmt_money(amount, currency=quotation.currency)

	rows = "".join(
		f'<div class="order-item"><span>{esc(row.description if totals.is_charge_item(row.item_code) else row.item_name)}</span>'
		f'<span class="order-qty">{cart_module.display_qty(row.qty)}</span><span class="order-amount">{esc(fmt(row.amount))}</span></div>'
		for row in quotation.items
	)
	included = sum(flt(t.tax_amount_after_discount_amount) for t in quotation.taxes if t.included_in_print_rate)
	added = "".join(
		f'<div class="order-line"><span>{esc(t.description)}</span><span>{esc(fmt(t.tax_amount))}</span></div>'
		for t in quotation.taxes if not t.included_in_print_rate and t.tax_amount
	)
	html = (
		rows + added
		+ f'<div class="order-line total"><span>{esc(_("Total"))}</span><span>{esc(fmt(quotation.grand_total))} {esc(quotation.currency)}</span></div>'
		+ (f'<p class="order-note">{esc(_("Includes {0} GST").format(fmt(included)))}</p>' if included else "")
	)
	expired = bool(quotation.valid_till and getdate(quotation.valid_till) < getdate(nowdate()))
	can_accept = quotation.docstatus == 1 and quotation.status not in ("Ordered", "Lost", "Cancelled") and not expired and not order
	methods = [m for m in quote_payment_methods(quotation)]
	return {
		"name": quotation.name,
		"token": token or "",
		"status": _("Ordered") if order else (_("Expired") if expired else (_("Being prepared") if quotation.docstatus == 0 else _("Ready to accept"))),
		"valid_till": frappe.utils.formatdate(quotation.valid_till) if quotation.valid_till else "",
		"lines_html": html,
		"can_accept": "true" if can_accept else None,
		"payment_methods": methods,
		"order_url": store_url(f"order-confirmation/{order}?token={frappe.db.get_value('Sales Order', order, 'shop_access_token') or ''}") if order else None,
	}


def quote_payment_methods(quotation) -> list[dict]:
	methods = [m for m in totals.payment_methods() if m["method"] in (totals.CARD, totals.BANK, totals.ACCOUNT)]
	fee = totals.card_fee_for(quotation.grand_total, is_export(quotation), quotation.taxes_and_charges)
	return [
		dict(m, fee_note=_("Card fee {0} added").format(totals.money(fee, quotation.currency)) if fee else "")
		if m["method"] == totals.CARD
		else m
		for m in methods
	]


def is_export(quotation) -> bool:
	country = frappe.db.get_value("Address", quotation.shipping_address_name, "country") if quotation.shipping_address_name else None
	return bool(country and country != totals.company_country())


@frappe.whitelist(allow_guest=True, methods=["POST"])
@rate_limit(limit=10, seconds=60)
def accept_quote(name: str, token: str, payment_method: str, accept_terms: int = 0) -> dict:
	"""The customer accepts the quote: the order is placed at the quoted prices, then paid."""
	from erpnext.selling.doctype.quotation.quotation import make_sales_order

	from shop.storefront.checkout import create_payment_request, elevated, set_shop_details, send_confirmation_for

	payment_method = totals.normalize_method(payment_method)
	settings = frappe.get_cached_doc("Shop Settings")
	if settings.get("require_terms") and not cint(accept_terms):
		frappe.throw(_("Please accept the terms and conditions"))
	with elevated():
		quotation = load_quote(name, token)
		summary = quote_summary(name, token)
		if not summary["can_accept"]:
			frappe.throw(_("This quote can no longer be accepted. Please contact us."))
		if payment_method not in [m["method"] for m in summary["payment_methods"]]:
			frappe.throw(_("This payment method is not available"))
		order = make_sales_order(quotation.name)
		order.delivery_date = add_days(nowdate(), 3)
		order.contact_email = quotation.contact_email
		order.shop_access_token = frappe.generate_hash(length=32)
		order_quote = frappe._dict(
			payment_method=payment_method,
			shipping=frappe._dict(json.loads(quotation.shop_shipping_option)) if quotation.get("shop_shipping_option") else None,
		)
		set_shop_details(order, order_quote, cint(accept_terms), 0)
		fee = totals.card_fee_for(quotation.grand_total, is_export(quotation), quotation.taxes_and_charges) if payment_method == totals.CARD else 0
		if fee and settings.get("card_fee_item"):
			order.append("items", {"item_code": settings.card_fee_item, "qty": 1, "rate": fee, "description": _("Card fee")})
		order.flags.ignore_permissions = True
		order.insert(ignore_permissions=True)
		order.submit()
		response = {"sales_order": order.name}
		confirmation = store_url(f"order-confirmation/{order.name}?token={order.shop_access_token}")
		if payment_method == totals.CARD:
			response["redirect"] = create_payment_request(order, {"email": order.contact_email}, order.shop_access_token)
		else:
			response["redirect"] = confirmation
			send_confirmation_for(order, confirmation)
	return response
