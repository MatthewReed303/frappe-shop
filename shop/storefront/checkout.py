import json
from contextlib import contextmanager

import frappe
from frappe import _
from frappe.rate_limiter import rate_limit
from frappe.utils import add_days, flt, nowdate, validate_email_address

from shop.routes import store_url
from shop.storefront import cart as cart_module
from shop.storefront import pricing, shipping, stock, totals


@frappe.whitelist(allow_guest=True)
def get_checkout_summary() -> dict:
	cart = cart_module.resolve_cart()
	settings = frappe.get_cached_doc("Shop Settings")
	return {
		"cart": cart_module.cart_payload(cart),
		"payment_methods": totals.payment_methods(),
		"currency": settings.currency,
		"prefill": checkout_prefill(),
		"addresses": saved_addresses(),
		"has_addresses": "true" if saved_addresses_exist() else None,
		"countries": countries(),
		"default_country": totals.company_country(),
		"terms": terms_details(),
	}


def countries() -> list[dict]:
	default = totals.company_country()
	names = frappe.get_all("Country", pluck="name", order_by="name")
	return [{"name": name, "selected": "selected" if name == default else None} for name in names]


def terms_details() -> dict | None:
	settings = frappe.get_cached_doc("Shop Settings")
	if not settings.get("require_terms"):
		return None
	url = settings.get("terms_url") or (store_url("terms") if settings.get("terms_text") else "")
	return {
		"url": url,
		"version": settings.get("terms_version") or "",
		"business_label": settings.get("business_buyer_label") or None,
	}


@frappe.whitelist(allow_guest=True, methods=["POST"])
@rate_limit(limit=60, seconds=60)
def quote(
	address: dict | None = None,
	shipping_option: str | None = None,
	payment_method: str | None = None,
	currency: str | None = None,
) -> dict:
	"""Totals for the checkout as the shopper fills it in: shipping options, fees, tax and total."""
	cart = cart_module.resolve_cart()
	if not cart or not cart.items:
		frappe.throw(_("Your cart is empty"))
	with elevated():
		result = totals.compute(cart, address, shipping_option, payment_method, currency)
		if result.shipping:
			shipping.remember_choice(cart, result.shipping["id"])
		return totals.payload(result)


PREFILL_FIELDS = (
	"email",
	"full_name",
	"phone",
	"address_line1",
	"address_line2",
	"city",
	"state",
	"country",
	"pincode",
)


def checkout_prefill() -> dict:
	"""Signed-in customers get their details back instead of an empty form."""
	prefill = dict.fromkeys(PREFILL_FIELDS, "")
	user = frappe.session.user
	if user in ("Guest", None, "Administrator"):
		return prefill
	prefill["email"] = user
	prefill["full_name"] = frappe.db.get_value("User", user, "full_name") or ""
	from shop.storefront.orders import session_customers

	customers = session_customers()
	if not customers:
		return prefill
	prefill["phone"] = contact_phone(user) or ""
	address = last_shipping_address(customers)
	for field, value in (address or {}).items():
		prefill[field] = value or ""
	return prefill


def contact_phone(user: str) -> str | None:
	contact = frappe.db.get_value("Contact Email", {"email_id": user}, "parent")
	if not contact:
		return None
	return frappe.db.get_value("Contact", contact, "mobile_no") or frappe.db.get_value(
		"Contact", contact, "phone"
	)


ADDRESS_FIELDS = ("address_line1", "address_line2", "city", "state", "country", "pincode")


def saved_addresses_exist() -> bool:
	return len(saved_addresses()) > 1


def saved_addresses() -> list[dict]:
	"""Every address this customer has shipped to, newest first, plus a blank entry."""
	from shop.storefront.orders import session_customers

	if frappe.session.user in ("Guest", None, "Administrator"):
		return []
	customers = session_customers()
	if not customers:
		return []
	links = frappe.get_all(
		"Dynamic Link",
		filters={"parenttype": "Address", "link_doctype": "Customer", "link_name": ["in", customers]},
		pluck="parent",
	)
	if not links:
		return []
	rows = frappe.get_all(
		"Address",
		filters={"name": ["in", links]},
		fields=["name", *ADDRESS_FIELDS],
		order_by="modified desc",
		limit=6,
	)
	for row in rows:
		row.line = ", ".join(str(row[field]) for field in ("address_line1", "city", "pincode") if row.get(field))
	rows.append(frappe._dict({"name": "", "line": _("Enter a new address")}))
	return rows


def last_shipping_address(customers: list[str]) -> dict | None:
	links = frappe.get_all(
		"Dynamic Link",
		filters={"parenttype": "Address", "link_doctype": "Customer", "link_name": ["in", customers]},
		pluck="parent",
	)
	if not links:
		return None
	rows = frappe.get_all(
		"Address",
		filters={"name": ["in", links]},
		fields=["address_line1", "address_line2", "city", "state", "country", "pincode"],
		order_by="modified desc",
		limit=1,
	)
	return rows[0] if rows else None


@frappe.whitelist(allow_guest=True, methods=["POST"])
# generous enough for shoppers sharing an office or campus network
@rate_limit(limit=30, seconds=60)
def place_order(
	customer: dict,
	address: dict,
	payment_method: str = "cod",
	shipping_option: str | None = None,
	currency: str | None = None,
	accept_terms: int = 0,
	business_buyer: int = 0,
	expected_total: float | None = None,
) -> dict:
	cart = cart_module.resolve_cart()
	payment_method = totals.normalize_method(payment_method)
	validate_order(cart, customer, payment_method, accept_terms)
	with elevated():
		order_quote = totals.compute(cart, address, shipping_option, payment_method, currency)
		validate_quote(order_quote, expected_total)
		party = get_or_create_customer(customer)
		shipping_address = create_address(party, customer, order_quote.address or address)
		sales_order = create_sales_order(
			cart,
			party,
			shipping_address,
			order_quote,
			terms_accepted=frappe.utils.cint(accept_terms),
			business_buyer=frappe.utils.cint(business_buyer),
			email=customer["email"].strip().lower(),
		)
		convert_cart(cart, sales_order)
		confirmation_url = store_url(f"order-confirmation/{sales_order.name}?token={cart.token}")
		response = {
			"sales_order": sales_order.name,
			"confirmation_url": confirmation_url,
		}
		if payment_method == totals.CARD:
			# the confirmation goes out once the card payment succeeds (ShopPaymentRequest)
			response["payment_url"] = create_payment_request(sales_order, customer, cart.token)
		else:
			send_confirmation_for(sales_order, confirmation_url)
	return response


def send_confirmation_for(sales_order, confirmation_url: str) -> None:
	"""Orders not paid by card: invoice on-account orders, then email the confirmation."""
	if sales_order.get("shop_payment_method") == totals.ACCOUNT:
		invoice_on_account(sales_order)
	queue_confirmation_email(sales_order, sales_order.contact_email, confirmation_url)


def invoice_on_account(sales_order) -> str:
	"""Invoice an order paid on account and send it to fulfilment straight away."""
	from erpnext.selling.doctype.sales_order.sales_order import make_sales_invoice

	from shop.fulfillment.service import auto_send

	invoice = make_sales_invoice(sales_order.name)
	invoice.flags.ignore_permissions = True
	invoice.insert(ignore_permissions=True)
	invoice.submit()
	sales_order.add_comment("Info", _("Invoiced on account: {0}.").format(invoice.name))
	auto_send(sales_order.name)
	return invoice.name


def send_confirmation(order_name: str) -> None:
	"""Email the order confirmation for an order placed through the store."""
	token = frappe.db.get_value("Shop Cart", {"sales_order": order_name}, "token")
	email = frappe.db.get_value("Sales Order", order_name, "contact_email")
	token = token or frappe.db.get_value("Sales Order", order_name, "shop_access_token")
	if not token or not email:
		return
	confirmation_url = store_url(f"order-confirmation/{order_name}?token={token}")
	queue_confirmation_email(frappe.get_doc("Sales Order", order_name), email, confirmation_url)


def validate_quote(order_quote, expected_total: float | None) -> None:
	if not order_quote.shipping:
		if order_quote.shipping_messages:
			frappe.throw("<br>".join(order_quote.shipping_messages))
		frappe.throw(_("We can't ship to this address. Check the address, or contact us."))
	if expected_total is not None and abs(flt(expected_total) - order_quote.total) > 0.009:
		frappe.throw(
			_("The total changed to {0}. Check your order and place it again.").format(
				totals.money(order_quote.total, order_quote.currency)
			),
			title=_("Total changed"),
		)


def queue_confirmation_email(sales_order, email: str, confirmation_url: str):
	try:
		from shop.storefront import payment

		bank = payment.method(sales_order) == totals.BANK and not payment.is_paid(sales_order)
		attachments = payment.invoice_attachments(payment.order_invoice(sales_order.name))
		frappe.sendmail(
			recipients=[email],
			attachments=attachments,
			subject=(
				_("Your order {0}: how to pay").format(sales_order.name)
				if bank
				else _("Your order {0} is confirmed").format(sales_order.name)
			),
			message=confirmation_email_html(sales_order, confirmation_url),
			reference_doctype="Sales Order",
			reference_name=sales_order.name,
		)
	except Exception:
		frappe.log_error(title="Order confirmation email failed")


def confirmation_email_html(sales_order, confirmation_url: str) -> str:
	from shop.storefront import payment

	settings = frappe.get_cached_doc("Shop Settings")
	esc = frappe.utils.escape_html

	def fmt(amount):
		return frappe.utils.fmt_money(amount, currency=sales_order.currency)

	def row(label, amount, bold=False):
		style = "padding:6px 0" + (";font-weight:bold;border-top:1px solid #ddd" if bold else "")
		return (
			f"<tr><td style='{style}'>{esc(label)}</td>"
			f"<td style='{style};text-align:right'>{esc(amount)}</td></tr>"
		)

	rows = "".join(
		row(f"{line.description or line.item_name}" if totals.is_charge_item(line.item_code) else f"{line.item_name} × {frappe.utils.cint(line.qty)}", fmt(line.amount))
		for line in sales_order.items
	)
	if sales_order.discount_amount:
		rows += row(_("Discount"), "-" + fmt(sales_order.discount_amount))
	for tax in sales_order.taxes:
		if not tax.included_in_print_rate and tax.tax_amount:
			rows += row(tax.description, fmt(tax.tax_amount))
	rows += row(_("Total"), f"{fmt(sales_order.grand_total)} {sales_order.currency}", bold=True)
	included = sum(frappe.utils.flt(t.tax_amount_after_discount_amount) for t in sales_order.taxes if t.included_in_print_rate)
	tax_note = f"<p style='color:#666;font-size:13px'>{esc(_('Includes {0} GST').format(fmt(included)))}</p>" if included else ""
	pay = ""
	if sales_order.get("shop_payment_method") == totals.ACCOUNT:
		invoice = frappe.db.get_value(
			"Sales Invoice Item", {"sales_order": sales_order.name, "docstatus": 1}, "parent"
		)
		if invoice:
			due = frappe.db.get_value("Sales Invoice", invoice, "due_date")
			pay = f"<p>{esc(_('Invoice {0} is attached, due {1}.').format(invoice, frappe.utils.formatdate(due)))}</p>"
	elif payment.is_paid(sales_order) and payment.order_invoice(sales_order.name):
		pay = f"<p>{esc(_('Paid. Your invoice {0} is attached.').format(payment.order_invoice(sales_order.name)))}</p>"
	if sales_order.get("shop_payment_method") == totals.CARD and not payment.is_paid(sales_order):
		token = frappe.db.get_value("Shop Cart", {"sales_order": sales_order.name}, "token")
		from shop.payments import stripe_checkout

		if token and stripe_checkout.is_stripe():
			pay = (
				f"<p>{esc(_('If you did not finish paying, you can pay here:'))} "
				f"<a href='{stripe_checkout.pay_url(sales_order.name, token)}'>{esc(_('Pay by card'))}</a></p>"
			)
	terms = ""
	if sales_order.get("shop_terms_version") and settings.get("terms_url"):
		terms = (
			f"<p style='color:#666;font-size:13px'>{esc(_('You accepted our terms and conditions ({0}):').format(sales_order.shop_terms_version))} "
			f"<a href='{frappe.utils.get_url(settings.terms_url)}'>{esc(frappe.utils.get_url(settings.terms_url))}</a></p>"
		)
	return f"""
	<p>{esc(_("Thank you for your order at {0}.").format(settings.store_name or _("our store")))}</p>
	<p>{esc(_("Order"))}: <b>{esc(sales_order.name)}</b></p>
	<table style="width:100%;max-width:520px;border-collapse:collapse">{rows}</table>
	{tax_note}
	{payment.bank_instructions_html(sales_order)}
	{pay}
	<p><a href="{frappe.utils.get_url(confirmation_url)}">{esc(_("View your order"))}</a></p>
	{terms}
	"""


@contextmanager
def elevated():
	# ERPNext's SO validation requires Item read perms no shopper role has.
	# Inputs are fully validated before elevation; scope approved for guest checkout.
	# set_user mutates session.sid/data in place, so restore the whole identity
	# or the response reissues a broken sid cookie and logs the shopper out.
	session = frappe.local.session
	original = (session.user, session.sid, session.data)
	# remember who is shopping, so their prices and accounts still apply while elevated
	outer = getattr(frappe.local, "shop_shopper", None)
	frappe.local.shop_shopper = outer or session.user
	frappe.set_user("Administrator")
	try:
		yield
	finally:
		frappe.local.shop_shopper = outer
		session.user, session.sid, session.data = original
		frappe.local.cache = {}
		frappe.local.role_permissions = {}


def validate_order(cart, customer: dict, payment_method: str, accept_terms: int = 0):
	if not cart or not cart.items:
		frappe.throw(_("Your cart is empty"))
	settings = frappe.get_cached_doc("Shop Settings")
	if payment_method not in [method["method"] for method in totals.payment_methods()]:
		frappe.throw(_("This payment method is not available"))
	if settings.get("require_terms") and not frappe.utils.cint(accept_terms):
		frappe.throw(_("Please accept the terms and conditions"))
	validate_email_address(customer.get("email"), throw=True)
	if not customer.get("full_name"):
		frappe.throw(_("Name is required"))
	validate_stock(cart)


def validate_stock(cart):
	settings = frappe.get_cached_doc("Shop Settings")
	for row in cart.items:
		cart_module.validate_purchasable(row.item_code)
		if settings.allow_out_of_stock:
			continue
		if not frappe.get_cached_value("Item", row.item_code, "is_stock_item"):
			continue
		available = stock.get_stock([row.item_code]).get(row.item_code, 0)
		if available < flt(row.qty):
			frappe.throw(_("Only {0} of {1} left in stock").format(int(available), row.item_code))


def get_or_create_customer(customer: dict) -> str:
	signed_in = pricing.session_customer()
	if signed_in:
		return signed_in  # a signed-in shopper always orders on their own account
	email = customer["email"].strip().lower()
	existing = find_customer_by_email(email)
	if existing:
		return existing
	party = frappe.get_doc(
		{
			"doctype": "Customer",
			"customer_name": customer["full_name"],
			"customer_type": "Individual",
		}
	).insert(ignore_permissions=True)
	create_contact(party.name, customer, email)
	return party.name


def find_customer_by_email(email: str) -> str | None:
	contact = frappe.db.get_value("Contact Email", {"email_id": email}, "parent")
	if not contact:
		return None
	return frappe.db.get_value(
		"Dynamic Link",
		{"parenttype": "Contact", "parent": contact, "link_doctype": "Customer"},
		"link_name",
	)


def create_contact(party: str, customer: dict, email: str):
	first_name, _space, last_name = customer["full_name"].strip().partition(" ")
	contact = frappe.get_doc(
		{
			"doctype": "Contact",
			"first_name": first_name,
			"last_name": last_name.strip() or None,
			"links": [{"link_doctype": "Customer", "link_name": party}],
		}
	)
	contact.add_email(email, is_primary=True)
	if customer.get("phone"):
		contact.add_phone(customer["phone"], is_primary_mobile_no=True)
	contact.insert(ignore_permissions=True)


def create_address(party: str, customer: dict, address: dict):
	existing = find_address(party, address)
	if existing:
		# bump modified so saved addresses stay ordered by last use
		frappe.db.set_value("Address", existing, "modified", frappe.utils.now())
		return frappe.get_doc("Address", existing)
	doc = frappe.get_doc(
		{
			"doctype": "Address",
			"address_title": customer["full_name"],
			"address_type": "Shipping",
			"address_line1": address.get("address_line1"),
			"address_line2": address.get("address_line2"),
			"city": address.get("city"),
			"state": address.get("state"),
			"country": address.get("country") or frappe.db.get_default("country"),
			"pincode": address.get("pincode"),
			"phone": customer.get("phone"),
			"email_id": customer.get("email"),
			"links": [{"link_doctype": "Customer", "link_name": party}],
		}
	)
	doc.insert(ignore_permissions=True)
	return doc


def find_address(party: str, address: dict) -> str | None:
	links = frappe.get_all(
		"Dynamic Link",
		filters={"parenttype": "Address", "link_doctype": "Customer", "link_name": party},
		pluck="parent",
	)
	if not links:
		return None
	return frappe.db.get_value(
		"Address",
		{
			"name": ["in", links],
			"address_line1": address.get("address_line1"),
			"city": address.get("city"),
			"pincode": address.get("pincode"),
		},
	)


def create_sales_order(
	cart,
	party: str,
	shipping_address,
	order_quote=None,
	terms_accepted: int = 0,
	business_buyer: int = 0,
	email: str | None = None,
):
	settings = frappe.get_cached_doc("Shop Settings")
	if order_quote is None:
		order_quote = totals.compute(cart, None)
	company_currency = frappe.get_cached_value("Company", settings.company, "default_currency")
	currency = order_quote.currency
	sales_order = frappe.get_doc(
		{
			"doctype": "Sales Order",
			"company": settings.company,
			"customer": party,
			"order_type": "Sales",
			"delivery_date": add_days(nowdate(), 3),
			"selling_price_list": pricing.customer_price_list(party),
			"currency": currency,
			"conversion_rate": totals.exchange_rate(currency, company_currency),
			"plc_conversion_rate": 1,
			"ignore_pricing_rule": 1,
			"disable_rounded_total": 1,
			"customer_address": shipping_address.name,
			"shipping_address_name": shipping_address.name,
			"items": [
				{
					"item_code": row.item_code,
					"qty": row.qty,
					"rate": row.rate,
					"warehouse": settings.default_warehouse,
				}
				for row in order_quote.order_items
			],
		}
	)
	if email:
		# the order's contact, for receipts and payment reminders
		contact = frappe.db.get_value("Contact Email", {"email_id": email}, "parent")
		sales_order.contact_person = contact
		sales_order.contact_email = email
	if order_quote.discount:
		sales_order.apply_discount_on = "Grand Total"
		sales_order.discount_amount = order_quote.discount
	apply_taxes(sales_order, settings, order_quote.tax_template)
	apply_shipping(sales_order, settings, order_quote)
	for fee in order_quote.fees:
		sales_order.append("items", {"item_code": fee.item, "qty": 1, "rate": fee.amount, "description": fee.label})
	set_shop_details(sales_order, order_quote, terms_accepted, business_buyer)
	sales_order.flags.ignore_permissions = True
	sales_order.insert(ignore_permissions=True)
	check_total(sales_order, order_quote)
	sales_order.submit()
	if order_quote.discount and cart.coupon_code:
		from shop.storefront import coupons

		coupons.redeem(cart.coupon_code)
	return sales_order


def set_shop_details(sales_order, order_quote, terms_accepted: int, business_buyer: int) -> None:
	settings = frappe.get_cached_doc("Shop Settings")
	if not frappe.get_meta("Sales Order").has_field("shop_payment_method"):
		return
	days = frappe.utils.cint(settings.get("bank_transfer_days")) or 7
	sales_order.shop_payment_method = order_quote.payment_method
	sales_order.shop_payment_due = add_days(nowdate(), days if order_quote.payment_method == totals.BANK else 2)
	if order_quote.shipping:
		sales_order.shop_shipping_option = json.dumps(
			{key: order_quote.shipping.get(key) for key in ("id", "label", "amount", "carrier", "service", "meta")},
			default=str,
		)
	if terms_accepted:
		sales_order.shop_terms_version = settings.get("terms_version")
		sales_order.shop_terms_accepted_on = frappe.utils.now_datetime()
	sales_order.shop_business_buyer = 1 if business_buyer else 0


def check_total(sales_order, order_quote) -> None:
	"""The order must cost what the shopper was shown."""
	if abs(flt(sales_order.grand_total) - flt(order_quote.total)) > 0.011 * max(len(sales_order.items), 1):
		frappe.log_error(
			title="Shop order total differs from checkout",
			message=f"{sales_order.grand_total} vs {order_quote.total}\n{frappe.as_json(order_quote, indent=1)}",
		)
		frappe.throw(_("We couldn't work out your total. Please try again or contact us."))


def apply_taxes(sales_order, settings, template: str | None = None):
	template = template or settings.tax_template
	if not template:
		return
	from shop.compat import get_sales_taxes

	sales_order.taxes_and_charges = template
	for tax in get_sales_taxes(template):
		sales_order.append("taxes", tax)


def apply_shipping(sales_order, settings, order_quote):
	if not order_quote.shipping or not order_quote.shipping.customer_amount:
		return
	amount = order_quote.shipping.customer_amount
	if settings.get("shipping_item"):
		sales_order.append(
			"items", {"item_code": settings.shipping_item, "qty": 1, "rate": amount, "description": order_quote.shipping["label"]}
		)
		return
	sales_order.append(
		"taxes",
		{
			"charge_type": "Actual",
			"account_head": settings.shipping_account,
			"description": order_quote.shipping["label"] or _("Shipping"),
			"tax_amount": amount,
		},
	)


def convert_cart(cart, sales_order):
	cart.status = "Converted"
	cart.sales_order = sales_order.name
	cart.save(ignore_permissions=True)


def create_payment_request(sales_order, customer: dict, token: str | None = None) -> str | None:
	from erpnext.accounts.doctype.payment_request.payment_request import make_payment_request

	from shop.payments import stripe_checkout

	settings = frappe.get_cached_doc("Shop Settings")
	payment_request = make_payment_request(
		dt="Sales Order",
		dn=sales_order.name,
		recipient_id=customer.get("email"),
		payment_gateway_account=settings.payment_gateway_account,
		submit_doc=1,
		mute_email=1,
		return_doc=1,
	)
	if token and stripe_checkout.is_stripe():
		return stripe_checkout.pay_url(sales_order.name, token)
	return payment_request.get_payment_url()
