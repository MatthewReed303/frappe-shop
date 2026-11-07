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
		"cart": cart_module.cart_payload(cart, money=pricing.store_amount),
		"payment_methods": totals.payment_methods(),
		"currency": settings.currency,
		"prefill": checkout_prefill(),
		"addresses": saved_addresses(),
		"has_addresses": "true" if saved_addresses_exist() else None,
		"countries": countries(),
		"default_country": totals.company_country(),
		"terms": terms_details(),
		"bank_currency_note": bank_currency_note(),
		"quotes_enabled": "true" if settings.get("allow_quote_requests") else None,
	}


def bank_currency_note() -> str | None:
	"""Explains the bank transfer currency choice, with the conversion fee the store charges."""
	accounts = totals.bank_accounts()
	if len(accounts) < 2:
		return None
	fees = sorted({flt(row.conversion_fee_percent) for row in accounts if flt(row.conversion_fee_percent)})
	note = _("We give you local bank details for the currency you choose.")
	if fees:
		fee = f"{fees[-1]:g}%" if len(fees) == 1 else _("up to {0}%").format(f"{fees[-1]:g}")
		note += " " + _("Paying in another currency than {0} adds {1} to cover converting the money.").format(
			totals.company_currency(), fee
		)
	return note


def countries() -> list[dict]:
	default = totals.company_country()
	names = frappe.get_all("Country", pluck="name", order_by="name")
	return [{"name": name, "selected": "selected" if name == default else None} for name in names]


def terms_details() -> dict | None:
	from shop.storefront import terms

	return terms.details()


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
	validate_order(cart, customer, payment_method, accept_terms)
	with elevated():
		lock_cart(cart)
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
		if payment_method == totals.GATEWAY and totals.order_total(sales_order) > 0:
			# the confirmation goes out once the payment succeeds (ShopPaymentRequest)
			response["payment_url"] = create_payment_request(sales_order, customer, cart.token)
		else:
			send_confirmation_for(sales_order, confirmation_url)
	return response


def lock_cart(cart) -> None:
	"""Hold the cart for this order, so a second submit can't order it twice."""
	status = frappe.db.get_value("Shop Cart", cart.name, "status", for_update=True)
	if status != "Active":
		frappe.throw(_("This cart has already been ordered. Check your email for the order."))


def send_confirmation_for(sales_order, confirmation_url: str) -> None:
	"""Orders not waiting on an online payment: invoice on-account orders, treat an order with
	nothing to pay as paid, then email the confirmation."""
	from shop.storefront import payment

	method = sales_order.get("shop_payment_method")
	if method == totals.ACCOUNT:
		invoice_on_account(sales_order)
	elif method in (totals.GATEWAY, totals.BANK) and totals.order_total(sales_order) <= 0:
		payment.order_paid(sales_order)
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
	from shop.storefront.orders import access_token, order_url

	email = frappe.db.get_value("Sales Order", order_name, "contact_email")
	if not email or not access_token(order_name):
		return
	queue_confirmation_email(frappe.get_doc("Sales Order", order_name), email, order_url(order_name))


def validate_quote(order_quote, expected_total: float | None) -> None:
	if not order_quote.shipping:
		if order_quote.shipping_messages:
			frappe.throw("<br>".join(order_quote.shipping_messages))
		frappe.throw(_("We can't ship to this address. Check the address, or contact us."))
	for item in order_quote.order.items:
		if not flt(item.rate) and not item.get("is_free_item") and not totals.is_charge_item(item.item_code):
			frappe.throw(_("{0} can't be ordered right now. Remove it to continue.").format(item.item_name or item.item_code))
	if expected_total is not None and abs(flt(expected_total) - order_quote.total) > 0.009:
		frappe.throw(
			_("The total is {0}. Check your order and place it again.").format(
				totals.money(order_quote.total, order_quote.currency)
			),
			title=_("Total changed"),
		)


def queue_confirmation_email(sales_order, email: str, confirmation_url: str):
	try:
		from shop.storefront import emails, payment

		bank = payment.method(sales_order) == totals.BANK and not payment.is_paid(sales_order)
		emails.send(
			"order_payment_instructions" if bank else "order_confirmed",
			email,
			sales_order,
			attachments=payment.invoice_attachments(payment.order_invoice(sales_order.name)),
			**order_email_parts(sales_order, confirmation_url),
		)
	except Exception:
		frappe.log_error(title="Order confirmation email failed")


def order_email_parts(sales_order, confirmation_url: str) -> dict:
	"""The pieces the order emails' templates show: lines and totals, how to pay, links, terms."""
	from shop.storefront import payment

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
		row(f"{line.item_name} × {cart_module.display_qty(line.qty)}", fmt(line.amount))
		for line in sales_order.items
		if not totals.is_charge_item(line.item_code)
	)
	lines, notes = totals.summary_lines(sales_order)
	rows += "".join(row(line["label"], fmt(line["amount"])) for line in lines[1:])
	rows += row(_("Total"), f"{fmt(totals.order_total(sales_order))} {sales_order.currency}", bold=True)
	tax_note = "".join(f"<p style='color:#666;font-size:13px'>{esc(note)}</p>" for note in notes)
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
	if sales_order.get("shop_payment_method") == totals.GATEWAY and not payment.is_paid(sales_order):
		pay = (
			f"<p>{esc(_('If you did not finish paying, you can pay from your order page:'))} "
			f"<a href='{frappe.utils.get_url(confirmation_url)}'>{esc(_('View your order'))}</a></p>"
		)
	terms = ""
	from shop.storefront import terms as store_terms

	terms_url = store_terms.url()
	if sales_order.get("shop_terms_version") and terms_url:
		terms = (
			f"<p style='color:#666;font-size:13px'>{esc(_('You accepted our terms and conditions ({0}):').format(sales_order.shop_terms_version))} "
			f"<a href='{frappe.utils.get_url(terms_url)}'>{esc(frappe.utils.get_url(terms_url))}</a></p>"
		)
	return {
		"lines_html": f"<table style='width:100%;max-width:520px;border-collapse:collapse'>{rows}</table>{tax_note}",
		"payment_html": payment.bank_instructions_html(sales_order) + pay,
		"order_url": frappe.utils.get_url(confirmation_url),
		"terms_html": terms,
	}


@contextmanager
def elevated():
	"""Run as Administrator for the storefront's own reads and writes: ERPNext's Sales Order,
	Quotation and pricing code needs permissions no shopper role has. Callers check what the
	shopper may do before entering it."""
	# set_user changes session.sid and data in place, so the whole identity is restored, or the
	# response would reissue a broken sid cookie and sign the shopper out
	session = frappe.local.session
	original = (session.user, session.sid, session.data)
	form_dict = frappe.local.form_dict
	# who is shopping, so their prices and accounts still apply while elevated
	outer = getattr(frappe.local, "shop_shopper", None)
	frappe.local.shop_shopper = outer or session.user
	frappe.set_user("Administrator")
	try:
		yield
	finally:
		frappe.local.shop_shopper = outer
		session.user, session.sid, session.data = original
		frappe.local.form_dict = form_dict
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
		# a guest can't place orders on someone's account, or see its prices and addresses
		if pricing.shopper() == "Guest" and frappe.db.exists("User", {"name": email, "enabled": 1}):
			frappe.throw(_("There is an account for {0}. Please sign in to order with it.").format(email), title=_("Sign in"))
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
	"""The customer an email belongs to: a Customer it is a Portal User of, else one linked to any
	Contact with that email."""
	portal = frappe.db.get_value("Portal User", {"parenttype": "Customer", "user": email}, "parent")
	if portal:
		return portal
	contacts = frappe.get_all("Contact Email", filters={"email_id": email}, pluck="parent")
	if not contacts:
		return None
	return frappe.db.get_value(
		"Dynamic Link",
		{"parenttype": "Contact", "parent": ["in", contacts], "link_doctype": "Customer"},
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
	set_primary_contact(party, contact)


def set_primary_contact(party: str, contact) -> None:
	"""Make the first contact the customer's Customer Primary Contact, as the desk would.

	ERPNext reads the customer's email from it, for statements and other emails."""
	if frappe.db.get_value("Customer", party, "customer_primary_contact"):
		return
	frappe.db.set_value(
		"Customer",
		party,
		{
			"customer_primary_contact": contact.name,
			"email_id": contact.email_id,
			"mobile_no": contact.mobile_no,
		},
	)


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
	"""Place the order the shopper was quoted, for their customer."""
	if order_quote is None:
		order_quote = totals.compute(cart)
	sales_order = order_quote.order
	sales_order.customer = party
	# keep the prices the shopper was quoted, which ERPNext worked out for them
	sales_order.ignore_pricing_rule = 1
	sales_order.customer_address = shipping_address.name
	sales_order.shipping_address_name = shipping_address.name
	if email:
		sales_order.contact_person = frappe.db.get_value("Contact Email", {"email_id": email}, "parent")
		sales_order.contact_email = email
	set_shop_details(sales_order, order_quote, terms_accepted, business_buyer)
	sales_order.flags.ignore_permissions = True
	sales_order.insert(ignore_permissions=True)
	check_total(sales_order, order_quote)
	check_credit_limit(sales_order)
	sales_order.submit()
	if order_quote.coupon and cart.coupon_code:
		from shop.storefront import coupons

		coupons.redeem(cart.coupon_code)
	return sales_order


def check_credit_limit(sales_order) -> None:
	"""ERPNext's credit limit check for the customer, with a message meant for the shopper."""
	from erpnext.selling.doctype.customer.customer import (
		get_credit_limit,
		get_customer_outstanding,
		is_sales_order_credit_check_bypassed,
	)

	customer, company = sales_order.customer, sales_order.company
	limit = flt(get_credit_limit(customer, company))
	if not limit or is_sales_order_credit_check_bypassed(customer, company):
		return
	if flt(get_customer_outstanding(customer, company)) + flt(sales_order.base_grand_total) > limit:
		frappe.throw(_("This order is over your account's credit limit. Please contact us to place it."))


def set_shop_details(sales_order, order_quote, terms_accepted: int, business_buyer: int) -> None:
	settings = frappe.get_cached_doc("Shop Settings")
	if not frappe.get_meta("Sales Order").has_field("shop_payment_method"):
		return
	method = order_quote.payment_method
	sales_order.shop_payment_method = method
	days = {
		totals.BANK: frappe.utils.cint(settings.get("bank_transfer_days")),
		totals.GATEWAY: frappe.utils.cint(settings.get("online_payment_days")),
	}.get(method)
	if days:
		sales_order.shop_payment_due = add_days(nowdate(), days)
	if order_quote.shipping:
		sales_order.shop_shipping_option = json.dumps(
			{key: order_quote.shipping.get(key) for key in ("id", "label", "amount", "carrier", "service", "meta")},
			default=str,
		)
	from shop.storefront import terms

	# an order made from a quote keeps the quote's terms
	if not (sales_order.get("tc_name") or sales_order.get("terms")):
		terms.apply_to(sales_order)
	if terms_accepted:
		sales_order.shop_terms_version = terms.version()
		sales_order.shop_terms_accepted_on = frappe.utils.now_datetime()
	sales_order.shop_business_buyer = 1 if business_buyer else 0


def check_total(sales_order, order_quote) -> None:
	"""The order must cost what the shopper was shown."""
	if abs(totals.order_total(sales_order) - flt(order_quote.total)) > 0.009:
		frappe.log_error(
			title="Shop order total differs from checkout",
			message=f"{totals.order_total(sales_order)} vs {order_quote.total}",
		)
		frappe.throw(_("We couldn't work out your total. Please try again or contact us."))


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
