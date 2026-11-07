import frappe
from frappe import _
from frappe.utils import cint

from shop.routes import store_url, token_matches
from shop.storefront import cart as cart_module
from shop.storefront import pricing

STATUS_LABELS = {
	"To Deliver and Bill": "Processing",
	"To Deliver": "Processing",
	"To Bill": "Shipped",
	"Completed": "Delivered",
	"On Hold": "On hold",
}


@frappe.whitelist()
def get_orders(start: int = 0, limit: int = 20) -> list[dict]:
	customers = session_customers()
	if not customers:
		return []
	orders = frappe.get_all(
		"Sales Order",
		filters={"customer": ["in", customers], "docstatus": 1},
		fields=["name", "transaction_date", "status", "grand_total", "currency"],
		order_by="creation desc",
		start=cint(start),
		limit=min(cint(limit) or 20, 50),
	)
	for order in orders:
		order.formatted_total = frappe.utils.fmt_money(order.grand_total, currency=order.currency)
		# the same status the order page shows: awaiting payment, preparing, shipped...
		from shop.storefront import payment

		doc = frappe.get_doc("Sales Order", order.name)
		order.display_status = display_status(doc, payment.is_paid(doc))
		order.formatted_date = frappe.utils.formatdate(order.transaction_date, "d MMM yyyy")
		order.url = store_url(f"order-confirmation/{order.name}")
	return orders


@frappe.whitelist()
def get_order(name: str) -> dict:
	order = frappe.get_doc("Sales Order", name)
	if order.customer not in session_customers():
		frappe.throw(_("Not permitted"), frappe.PermissionError)
	return order_summary(order)


@frappe.whitelist(allow_guest=True)
def get_order_summary(name: str, token: str | None = None) -> dict:
	if not can_view(name, token):
		frappe.throw(_("Not permitted"), frappe.PermissionError)
	return order_summary(frappe.get_doc("Sales Order", name), token)


def valid_token(name: str, token: str | None) -> bool:
	"""The cart token of the checkout that placed the order, or the order's own access token
	(orders made from an accepted quote)."""
	if not token or not name:
		return False
	if frappe.db.exists("Shop Cart", {"token": token, "sales_order": name}):
		return True
	return bool(
		frappe.get_meta("Sales Order").has_field("shop_access_token")
		and token_matches(frappe.db.get_value("Sales Order", name, "shop_access_token"), token)
	)


def access_token(order_name: str) -> str | None:
	"""The secret that opens an order's page without signing in: the checkout's cart token, or the
	order's own token (orders made from an accepted quote)."""
	token = frappe.db.get_value("Shop Cart", {"sales_order": order_name}, "token")
	if not token and frappe.get_meta("Sales Order").has_field("shop_access_token"):
		token = frappe.db.get_value("Sales Order", order_name, "shop_access_token")
	return token


def order_url(order_name: str) -> str:
	"""The order's page, with its token when it has one."""
	token = access_token(order_name)
	return store_url(f"order-confirmation/{order_name}" + (f"?token={token}" if token else ""))


def can_view(name: str, token: str | None) -> bool:
	"""The cart token doubles as the guest secret; the signed-in owner needs no token."""
	if valid_token(name, token):
		return True
	customers = session_customers()
	return bool(customers) and frappe.db.get_value("Sales Order", name, "customer") in customers


def order_summary(order, token: str | None = None) -> dict:
	from shop.storefront import payment, returns, totals

	shipment = shipment_summary(order.name)

	def fmt(amount):
		return frappe.utils.fmt_money(amount, currency=order.currency)

	products = [row for row in order.items if not totals.is_charge_item(row.item_code)]
	charges = [row for row in order.items if totals.is_charge_item(row.item_code)]
	lines, notes = totals.summary_lines(order)
	product_total = sum(frappe.utils.flt(row.amount) for row in products)
	paid = payment.is_paid(order)
	method = payment.method(order)
	return {
		"returns": returns.summary(order.name),
		"name": order.name,
		"status": order.status,
		"display_status": display_status(order, paid),
		# signed in, the account lists every order; a guest keeps this page's own link
		"track_url": store_url("account/orders") if session_customers() else order_url(order.name),
		# an order waiting on an online payment is emailed once it is paid
		"confirmation_note": _("Your order is waiting for payment.")
		if order.docstatus == 1 and not paid and method == totals.GATEWAY
		else _("We've emailed you a copy of this order."),
		"progress": order_progress(order, shipment),
		"shipment": shipment,
		# paid, or to be paid on account or on delivery, and not sent yet
		"awaiting_shipment": "true"
		if not shipment and order.docstatus == 1 and (paid or method in (totals.ACCOUNT, totals.COD, None))
		else None,
		"transaction_date": str(order.transaction_date),
		"currency": order.currency,
		"total": product_total,
		"formatted_total": fmt(product_total),
		"formatted_shipping": shipping_label(order, charges),
		"discount_amount": order.discount_amount or None,
		"formatted_discount": fmt(order.discount_amount) if order.discount_amount else None,
		"grand_total": totals.order_total(order),
		"formatted_grand_total": fmt(totals.order_total(order)),
		"charges": [
			{"label": row.description or row.item_name, "formatted_amount": fmt(row.amount)} for row in charges
		],
		"taxes": [
			{"description": tax.description, "amount": tax.tax_amount, "formatted_amount": fmt(tax.tax_amount)}
			for tax in order.taxes
			if not tax.included_in_print_rate and tax.tax_amount
		],
		"tax_note": " ".join(notes) or None,
		"payment_method": method,
		"paid": "true" if paid else None,
		"unpaid": None if paid or order.docstatus == 2 else "true",
		"bank_transfer": None if paid or order.docstatus == 2 else payment.bank_instructions(order),
		"pay_url": pay_url(order) if not paid and method == totals.GATEWAY and order.docstatus == 1 else None,
		"totals_html": totals_html(order, lines, notes, charges),
		"payment_html": payment_html(order, paid, method, token),
		"items": [
			{
				"item_code": row.item_code,
				"item_name": row.item_name,
				"qty": cart_module.display_qty(row.qty),
				"rate": row.rate,
				"formatted_rate": fmt(row.rate),
				"amount": row.amount,
				"formatted_amount": fmt(row.amount),
				"image": row.image,
			}
			for row in products
		],
	}


def totals_html(order, lines: list[dict], notes: list[str], charges) -> str:
	"""The order's money lines as ready-made HTML, so every theme shows the same breakdown."""
	from shop.storefront import totals

	esc = frappe.utils.escape_html

	def fmt(amount):
		return frappe.utils.fmt_money(amount, currency=order.currency)

	def line(label, amount, kind=""):
		return f'<div class="order-line {kind}"><span>{esc(label)}</span><span>{esc(amount)}</span></div>'

	html = line(lines[0]["label"], fmt(lines[0]["amount"]))
	if not any(row["kind"] == "shipping" for row in lines):
		html += line(_("Shipping"), shipping_label(order, charges))
	for row in lines[1:]:
		html += line(row["label"], fmt(row["amount"]), row["kind"])
	html += line(_("Total"), f"{fmt(totals.order_total(order))} {order.currency}", "total")
	return html + "".join(f'<p class="order-note">{esc(note)}</p>' for note in notes)


def invoice_url(invoice: str, order_name: str, token: str | None) -> str:
	"""The invoice PDF link; the order's private token lets guests open it without signing in."""
	from urllib.parse import urlencode

	params = {"name": invoice}
	if token:
		params.update(order=order_name, token=token)
	return "/api/method/shop.storefront.accounts.invoice_pdf?" + urlencode(params)


def payment_html(order, paid: bool, method: str | None, token: str | None) -> str:
	from shop.storefront import payment, totals

	esc = frappe.utils.escape_html
	if is_cancelled(order):
		return f'<div class="order-payment cancelled"><p>{esc(_("This order has been cancelled."))}</p></div>'
	if paid:
		invoice = payment.order_invoice(order.name)
		button = (
			f'<a class="order-pay-button" href="{esc(invoice_url(invoice, order.name, token))}">{esc(_("Download invoice"))}</a>'
			if invoice
			else ""
		)
		return f'<div class="order-payment paid"><p>{esc(_("Payment received. Thank you."))}</p>{button}</div>'
	if method == totals.ACCOUNT:
		invoice = payment.order_invoice(order.name)
		if not invoice:
			return ""
		due = frappe.db.get_value("Sales Invoice", invoice, "due_date")
		return (
			f'<div class="order-payment account"><p>{esc(_("On account: invoice {0}, due {1}.").format(invoice, frappe.utils.formatdate(due)))}</p>'
			f'<a class="order-pay-button" href="{esc(invoice_url(invoice, order.name, token))}">{esc(_("Download invoice"))}</a></div>'
		)
	if method in totals.BANK_METHODS:
		info = payment.bank_instructions(order)
		if not info:
			return ""
		details = "".join(f"<div>{esc(line)}</div>" for line in info["details"])
		due = f'<p>{esc(_("Please pay by {0}. Unpaid orders are cancelled after that date.").format(info["due"]))}</p>' if info["due"] else ""
		return (
			f'<div class="order-payment bank"><h3>{esc(_("Pay by bank transfer"))}</h3>'
			f'<p>{esc(_("Amount"))}: <b>{esc(info["amount"])} {esc(info["currency"])}</b><br>'
			f'{esc(_("Reference"))}: <b>{esc(info["reference"])}</b></p>'
			f'<div class="bank-details">{details}</div>{due}'
			f'<p>{esc(_("We send your order as soon as the payment arrives."))}</p></div>'
		)
	url = pay_url(order) if method == totals.GATEWAY else None
	if url:
		return (
			f'<div class="order-payment card"><p>{esc(_("This order is waiting for payment."))}</p>'
			f'<a class="order-pay-button" href="{esc(url)}">{esc(_("Pay now"))}</a></div>'
		)
	return ""


def display_status(order, paid: bool) -> str:
	from shop.storefront import totals

	if is_cancelled(order):
		return _("Cancelled")
	if not paid and order.get("shop_payment_method") in (*totals.BANK_METHODS, totals.GATEWAY):
		return _("Awaiting payment")
	shipment = shipment_summary(order.name)
	if shipment and shipment.status in ("Pending", "Accepted"):
		return _("Preparing your order")
	if shipment and shipment.status == "Shipped":
		return _("Shipped")
	if shipment and shipment.status == "Delivered":
		return _("Delivered")
	return STATUS_LABELS.get(order.status, order.status)


def pay_url(order) -> str | None:
	"""Where to pay an order still owed online: its Payment Request's gateway page. Only shown to
	whoever may see the order."""
	return frappe.db.get_value(
		"Payment Request",
		{"reference_doctype": "Sales Order", "reference_name": order.name, "docstatus": 1, "status": ["!=", "Paid"]},
		"payment_url",
		order_by="creation desc",
	)


def is_cancelled(order) -> bool:
	"""Cancelled, or closed by the store when it cancelled an order already paid or invoiced."""
	from shop.storefront.payment import CANCELLED_BY_STORE

	return order.docstatus == 2 or (
		order.status == "Closed"
		and bool(
			frappe.db.exists(
				"Comment",
				{"reference_doctype": "Sales Order", "reference_name": order.name, "content": ["like", f"{CANCELLED_BY_STORE}%"]},
			)
		)
	)


def order_progress(order, shipment: dict | None) -> list[dict]:
	if is_cancelled(order):
		return [{"label": _("Cancelled"), "done": "true"}]
	from shop.storefront import payment, totals

	paid = bool(order.advance_paid) or has_payment(order.name) or payment.is_paid(order)
	# with a fulfilment, "shipped" means the courier has it, not that the stock was delivered
	if shipment:
		shipped = bool(shipment.get("shipped_on")) or shipment.get("status") in ("Shipped", "Delivered")
	else:
		shipped = (order.per_delivered or 0) >= 100
	delivered = bool(shipment and shipment.get("status") == "Delivered")
	method = order.get("shop_payment_method")
	if method == totals.ACCOUNT and not paid:
		payment_label, paid = _("Invoiced"), True
	elif paid:
		payment_label = _("Paid")
	elif method in (*totals.BANK_METHODS, totals.GATEWAY) or expects_online_payment(order.name):
		payment_label = _("Awaiting payment")
	else:
		payment_label = _("Payment on delivery")
	stages = [
		(_("Order placed"), True),
		(payment_label, paid),
		(_("Shipped"), shipped),
		(_("Delivered"), delivered),
	]
	return [{"label": label, "done": "true" if done else "false"} for label, done in stages]


def expects_online_payment(order_name: str) -> bool:
	return bool(
		frappe.db.exists(
			"Payment Request",
			{"reference_doctype": "Sales Order", "reference_name": order_name, "docstatus": 1},
		)
	)


def has_payment(order_name: str) -> bool:
	return bool(
		frappe.db.exists(
			"Payment Entry Reference",
			{"reference_doctype": "Sales Order", "reference_name": order_name, "docstatus": 1},
		)
	)


def shipment_summary(order_name: str) -> dict | None:
	rows = frappe.get_all(
		"Shop Fulfillment",
		filters={"sales_order": order_name, "status": ["!=", "Cancelled"]},
		fields=["status", "carrier", "tracking_number", "tracking_url", "shipped_on"],
		order_by="creation desc",
		limit=1,
	)
	if not rows:
		return None
	shipment = rows[0]
	shipment.shipped_on = str(shipment.shipped_on) if shipment.shipped_on else None
	shipment.line = " · ".join(part for part in (shipment.carrier, shipment.tracking_number) if part)
	return shipment


def shipping_label(order, charges=None) -> str:
	from shop.storefront import totals

	shipping_item = frappe.get_cached_doc("Shop Settings").get("shipping_item")
	charge = sum(row.amount for row in (charges or []) if shipping_item and row.item_code == shipping_item)
	shipping_account = frappe.get_cached_doc("Shop Settings").get("shipping_account")
	charge += sum(
		tax.tax_amount
		for tax in order.taxes
		if (shipping_account and tax.account_head == shipping_account and tax.charge_type == "Actual")
		or (tax.description or "").strip().lower() == "shipping"
	)
	return frappe.utils.fmt_money(charge, currency=order.currency) if charge else _("Free")


def session_customers(user: str | None = None) -> list[str]:
	user = user or getattr(frappe.local, "shop_shopper", None) or frappe.session.user
	if user in ("Guest", None):
		return []
	# customers the user is a Portal User of come first: they were given the account explicitly
	customers = frappe.get_all("Portal User", filters={"parenttype": "Customer", "user": user}, pluck="parent")
	contacts = frappe.get_all("Contact Email", filters={"email_id": user}, pluck="parent")
	if contacts:
		customers += frappe.get_all(
			"Dynamic Link",
			filters={"parenttype": "Contact", "parent": ["in", contacts], "link_doctype": "Customer"},
			pluck="link_name",
		)
	return list(dict.fromkeys(customers))
