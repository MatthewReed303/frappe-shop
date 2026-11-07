import frappe
from frappe import _
from frappe.utils import add_days, flt, get_datetime, now_datetime

from shop.storefront import orders as orders_module

RETURN_WINDOW_DAYS = 14
OPEN_STATUSES = ("Requested", "Approved")


@frappe.whitelist(allow_guest=True, methods=["POST"])
def create_request(
	order: str,
	item_code: str,
	request_type: str,
	reason: str,
	qty: float = 1,
	token: str | None = None,
) -> dict:
	if not orders_module.can_view(order, token):
		frappe.throw(_("Not permitted"), frappe.PermissionError)
	if request_type not in ("Return", "Replacement"):
		frappe.throw(_("Choose return or replacement"))
	if not (reason or "").strip():
		frappe.throw(_("Please tell us what went wrong"))
	doc = frappe.get_doc("Sales Order", order)
	validate_item(doc, item_code, qty)
	validate_window(doc)
	if frappe.db.exists(
		"Shop Return Request",
		{"sales_order": order, "item_code": item_code, "status": ["in", OPEN_STATUSES]},
	):
		frappe.throw(_("There is already an open request for this item"))
	request = frappe.get_doc(
		{
			"doctype": "Shop Return Request",
			"sales_order": order,
			"item_code": item_code,
			"request_type": request_type,
			"qty": flt(qty) or 1,
			"reason": reason.strip(),
		}
	).insert(ignore_permissions=True)
	notify(request, "return_requested", doc.contact_email)
	from shop.storefront.accounts import staff_emails

	notify(request, "return_staff", staff_emails(), reason=request.reason)
	return {"name": request.name, "status": request.status}


def validate_item(order, item_code: str, qty: float):
	row = next((item for item in order.items if item.item_code == item_code), None)
	if not row:
		frappe.throw(_("That item is not part of this order"))
	if flt(qty) > flt(row.qty):
		frappe.throw(_("You ordered only {0} of this item").format(int(row.qty)))


def validate_window(order):
	shipped_on = shipped_date(order.name)
	if not shipped_on:
		frappe.throw(_("Returns open once the order has shipped"))
	if now_datetime() > get_datetime(add_days(shipped_on, RETURN_WINDOW_DAYS)):
		frappe.throw(_("The {0}-day return window for this order has closed").format(RETURN_WINDOW_DAYS))


def shipped_date(order_name: str):
	"""When the order left: the courier's pickup for an order sent through a fulfilment provider,
	else its first Delivery Note."""
	shipment = orders_module.shipment_summary(order_name)
	if shipment:
		return get_datetime(shipment["shipped_on"]) if shipment.get("shipped_on") else None
	delivered = frappe.db.get_value(
		"Delivery Note Item",
		{"against_sales_order": order_name, "docstatus": 1},
		"creation",
		order_by="creation asc",
	)
	return get_datetime(delivered) if delivered else None


def summary(order_name: str) -> dict:
	"""What the confirmation page needs: open/closed requests and whether new ones are allowed."""
	requests = frappe.get_all(
		"Shop Return Request",
		filters={"sales_order": order_name},
		fields=["name", "item_name", "request_type", "status", "resolution_note"],
		order_by="creation desc",
	)
	for request in requests:
		request.line = f"{request.request_type} · {request.item_name}"
	shipped_on = shipped_date(order_name)
	eligible = bool(shipped_on and now_datetime() <= get_datetime(add_days(shipped_on, RETURN_WINDOW_DAYS)))
	return {
		"requests": requests,
		"eligible": "true" if eligible else None,
		"has_requests": "true" if requests else None,
		"show": "true" if requests or eligible else None,
		"window_days": RETURN_WINDOW_DAYS,
	}


def notify(request, key: str, recipients, attachments=None, **context) -> None:
	from shop.storefront import emails

	order = frappe.get_doc("Sales Order", request.sales_order)
	emails.send(
		key,
		recipients,
		order,
		attachments=attachments,
		request_type=request.request_type,
		item_name=request.item_name,
		qty=frappe.utils.cint(request.qty) if flt(request.qty) == int(flt(request.qty)) else request.qty,
		note=request.resolution_note,
		**context,
	)


def status_changed(request) -> None:
	"""Tell the customer; a completed return puts the stock back, credits the invoice and refunds."""
	order_email = frappe.db.get_value("Sales Order", request.sales_order, "contact_email")
	if request.status == "Approved":
		notify(request, "return_approved", order_email)
	elif request.status == "Rejected":
		notify(request, "return_rejected", order_email)
	elif request.status == "Completed":
		refunded = False
		if request.request_type == "Return":
			return_stock(request)
			credit(request)
			refunded = refund(request)
		amount = (
			frappe.utils.fmt_money(
				abs(flt(frappe.db.get_value("Sales Invoice", request.credit_note, "grand_total"))),
				currency=frappe.db.get_value("Sales Invoice", request.credit_note, "currency"),
			)
			if request.credit_note
			else ""
		)
		from shop.storefront.payment import invoice_attachments

		notify(
			request,
			"return_completed",
			order_email,
			attachments=invoice_attachments(request.credit_note) if request.credit_note else None,
			credit_note=request.credit_note,
			amount=amount,
			refunded=refunded,
		)


def return_lines(doc, request) -> None:
	"""Keep only the returned item on a return made from a Delivery Note or invoice, for the quantity
	returned."""
	rows = [row for row in doc.items if row.item_code == request.item_code]
	if not rows:
		frappe.throw(_("{0} is not on {1}").format(request.item_name, doc.return_against))
	row = rows[0]
	row.qty = -min(flt(request.qty), abs(flt(row.qty)))
	doc.items = [row]


def return_stock(request) -> None:
	"""ERPNext's return Delivery Note for the item, so the stock is back."""
	from erpnext.stock.doctype.delivery_note.delivery_note import make_sales_return

	if request.return_delivery_note:
		return
	note = frappe.db.get_value(
		"Delivery Note Item",
		{
			"against_sales_order": request.sales_order,
			"item_code": request.item_code,
			"docstatus": 1,
			"qty": [">", 0],
		},
		"parent",
		order_by="creation desc",
	)
	if not note:
		return
	returned = make_sales_return(note)
	return_lines(returned, request)
	returned.flags.ignore_permissions = True
	returned.insert(ignore_permissions=True)
	returned.submit()
	request.db_set("return_delivery_note", returned.name)


def credit(request) -> None:
	"""ERPNext's credit note (a return Sales Invoice) for the item."""
	from erpnext.accounts.doctype.sales_invoice.sales_invoice import make_sales_return

	from shop.storefront.payment import order_invoice

	if request.credit_note:
		return
	invoice = order_invoice(request.sales_order)
	if not invoice:
		return
	note = make_sales_return(invoice)
	return_lines(note, request)
	note.flags.ignore_permissions = True
	note.insert(ignore_permissions=True)
	note.submit()
	request.db_set("credit_note", note.name)
	# the documents are kept even if the refund that follows fails, so it is never made twice
	frappe.db.commit()


def refund(request) -> bool:
	"""Refund an order paid online through its gateway, when the gateway can, and record it as a
	Payment Entry against the credit note. Returns whether the money went back."""
	from erpnext.accounts.doctype.payment_entry.payment_entry import get_payment_entry

	if request.refund:
		return True
	if not request.credit_note:
		return False
	request_name = frappe.db.get_value(
		"Payment Request",
		{
			"reference_doctype": "Sales Order",
			"reference_name": request.sales_order,
			"status": "Paid",
			"docstatus": 1,
		},
		"name",
	)
	if not request_name:
		return False
	payment_request = frappe.get_doc("Payment Request", request_name)
	gateway = frappe.get_doc("Payment Gateway", payment_request.payment_gateway)
	controller = frappe.get_doc(gateway.gateway_settings, gateway.gateway_controller)
	if not hasattr(controller, "refund"):
		return False
	credit_note = frappe.get_doc("Sales Invoice", request.credit_note)
	amount = abs(flt(credit_note.grand_total))
	reference = controller.refund("Payment Request", payment_request.name, amount, credit_note.name)
	entry = get_payment_entry("Sales Invoice", credit_note.name, bank_account=payment_request.payment_account)
	entry.reference_no, entry.reference_date = reference, frappe.utils.nowdate()
	entry.flags.ignore_permissions = True
	entry.insert(ignore_permissions=True)
	entry.submit()
	request.db_set("refund", entry.name)
	return True
