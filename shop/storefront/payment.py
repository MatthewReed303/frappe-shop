"""How an order is paid: bank transfer instructions, payment status and overdue orders."""

import frappe
from frappe import _
from frappe.utils import flt, getdate, nowdate

from shop.storefront import totals


def amount_due(order) -> float:
	"""What is still owed, in the order currency."""
	total = totals.order_total(order)
	paid = flt(order.advance_paid)
	if not paid:
		return total
	if order.get("advance_payment_status") == "Fully Paid":
		return 0.0
	# ERPNext keeps advance_paid in the customer's account currency
	if (order.get("party_account_currency") or order.currency) != order.currency:
		paid = paid / (flt(order.conversion_rate) or 1)
	return max(flt(total - paid, order.precision("grand_total")), 0.0)


def is_paid(order) -> bool:
	if amount_due(order) <= 0:
		return True
	return bool(
		frappe.db.exists(
			"Payment Request",
			{"reference_doctype": "Sales Order", "reference_name": order.name, "status": "Paid", "docstatus": 1},
		)
	)


def method(order) -> str | None:
	return order.get("shop_payment_method") or None


def bank_instructions(order) -> dict | None:
	"""What to pay, where, and with which reference, for a bank transfer order."""
	if method(order) != totals.BANK:
		return None
	account = totals.bank_account(order.currency)
	if not account:
		return None
	lines = account.details
	return {
		"currency": order.currency,
		"label": account.label or order.currency,
		"amount": totals.money(amount_due(order), order.currency),
		"reference": order.name,
		"details": lines,
		"due": frappe.utils.formatdate(order.get("shop_payment_due")) if order.get("shop_payment_due") else "",
	}


def bank_instructions_html(order) -> str:
	info = bank_instructions(order)
	if not info:
		return ""
	esc = frappe.utils.escape_html
	details = "".join(f"<div>{esc(line)}</div>" for line in info["details"])
	due = (
		f"<p>{esc(_('Please pay by {0}. Unpaid orders are cancelled after that date.').format(info['due']))}</p>"
		if info["due"]
		else ""
	)
	return f"""
	<div style="border:1px solid #ddd;border-radius:6px;padding:14px;margin:16px 0">
		<p style="margin:0 0 8px;font-weight:bold">{esc(_("Pay by bank transfer"))}</p>
		<p style="margin:0 0 8px">{esc(_("Amount"))}: <b>{esc(info["amount"])}</b> ({esc(info["currency"])})<br>
		{esc(_("Reference"))}: <b>{esc(info["reference"])}</b></p>
		{details}
		{due}
		<p style="margin:8px 0 0">{esc(_("We'll send your order as soon as the payment arrives."))}</p>
	</div>
	"""


def on_payment_entry(doc, method=None) -> None:
	"""When a payment pays off a store order: invoice it, tell the customer once, hand it on."""
	for row in doc.references or []:
		if row.reference_doctype != "Sales Order" or not frappe.db.get_value(
			"Sales Order", row.reference_name, "shop_payment_method"
		):
			continue
		order = frappe.get_doc("Sales Order", row.reference_name)
		if order.docstatus != 1 or amount_due(order) > 0:
			continue
		if was_paid_before(order, row):
			continue
		order_paid(order)
		if order.shop_payment_method == totals.BANK:
			order.add_comment("Info", _("Bank transfer received ({0}).").format(doc.name))
			notify_paid(order)


def was_paid_before(order, reference) -> bool:
	"""Whether the order was already paid in full before this payment."""
	paid, allocated = flt(order.advance_paid), flt(reference.allocated_amount)
	if (order.get("party_account_currency") or order.currency) != order.currency:
		paid, allocated = paid / (flt(order.conversion_rate) or 1), allocated / (flt(order.conversion_rate) or 1)
	return totals.order_total(order) - (paid - allocated) <= 0


def order_paid(order) -> None:
	"""A store order is paid: invoice it when the store invoices paid orders, and send it on."""
	from shop.fulfillment.service import auto_send

	if frappe.get_cached_doc("Shop Settings").get("invoice_paid_orders"):
		invoice_paid_order(order)
	auto_send(order.name)


def notify_paid(order) -> None:
	from shop.storefront import emails

	invoice = order_invoice(order.name)
	emails.send("payment_received", order.contact_email, order, attachments=invoice_attachments(invoice), invoice=invoice)


def order_invoice(order_name: str) -> str | None:
	"""The submitted Sales Invoice made from this order, if there is one."""
	return frappe.db.get_value("Sales Invoice Item", {"sales_order": order_name, "docstatus": 1}, "parent")


def invoice_paid_order(order) -> str | None:
	"""Invoice a store order once it is paid in full, with the payment set against the invoice.

	ERPNext's own Sales Invoice is made from the Sales Order, and its Allocate Advances
	Automatically option takes the payment already recorded against the order. Orders paid on
	account are invoiced when they are placed. A failure is logged and never blocks the payment."""
	if order.get("shop_payment_method") not in (totals.GATEWAY, totals.BANK, totals.COD):
		return None
	existing = order_invoice(order.name)
	if existing or flt(order.per_billed) >= 100:
		return existing
	from erpnext.selling.doctype.sales_order.sales_order import make_sales_invoice

	frappe.db.savepoint("shop_invoice")
	try:
		invoice = make_sales_invoice(order.name)
		# as in the desk: ERPNext allocates the order's payment itself, after working out the totals
		invoice.allocate_advances_automatically = 1
		invoice.only_include_allocated_payments = 1
		invoice.flags.ignore_permissions = True
		invoice.insert(ignore_permissions=True)
		invoice.submit()
	except Exception:
		frappe.db.rollback(save_point="shop_invoice")
		frappe.log_error(title=f"Could not invoice paid order {order.name}")
		order.add_comment("Info", _("The order is paid but could not be invoiced automatically. Make the invoice from the order."))
		return None
	order.add_comment("Info", _("Paid and invoiced: {0}.").format(invoice.name))
	return invoice.name


def invoice_attachments(invoice: str | None) -> list:
	if not invoice:
		return []
	try:
		return [frappe.attach_print("Sales Invoice", invoice, file_name=invoice)]
	except Exception:
		# the email still goes out; the invoice is on the customer's account and order page
		frappe.log_error(title=f"Could not attach invoice {invoice}")
		return []


def cancel_overdue_orders() -> None:
	"""Daily: cancel store orders that were never paid by their due date, and tell the customer."""
	if not frappe.get_cached_doc("Shop Settings").get("cancel_unpaid_orders"):
		return
	if not frappe.get_meta("Sales Order").has_field("shop_payment_method"):
		return
	for name in frappe.get_all(
		"Sales Order",
		filters={
			"docstatus": 1,
			"shop_payment_method": ["in", [totals.BANK, totals.GATEWAY]],
			"shop_payment_due": ["<", nowdate()],
			"status": ["not in", ["Closed", "Completed"]],
		},
		pluck="name",
	):
		try:
			cancel_if_unpaid(name)
			frappe.db.commit()
		except Exception:
			frappe.db.rollback()
			frappe.log_error(title=f"Could not cancel unpaid order {name}")


def cancel_if_unpaid(name: str) -> bool:
	order = frappe.get_doc("Sales Order", name)
	if order.docstatus != 1 or is_paid(order) or flt(order.advance_paid) > 0:
		return False
	if order.get("shop_payment_due") and getdate(order.shop_payment_due) >= getdate(nowdate()):
		return False
	if flt(order.per_delivered) > 0 or flt(order.per_billed) > 0:
		return False
	for request in frappe.get_all(
		"Payment Request",
		filters={"reference_doctype": "Sales Order", "reference_name": name, "docstatus": 1, "status": ["!=", "Paid"]},
		pluck="name",
	):
		frappe.get_doc("Payment Request", request).cancel()
	order.flags.ignore_permissions = True
	order.cancel()
	order.add_comment("Info", _("Cancelled automatically: not paid by {0}.").format(order.shop_payment_due))
	notify_cancelled(order)
	return True


def notify_cancelled(order) -> None:
	from shop.storefront import emails

	emails.send("order_cancelled", order.contact_email, order, due_date=frappe.utils.formatdate(order.shop_payment_due))
