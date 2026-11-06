"""How an order is paid: bank transfer instructions, payment status and overdue orders."""

import frappe
from frappe import _
from frappe.utils import flt, getdate, nowdate

from shop.storefront import totals


def amount_due(order) -> float:
	"""What is still owed, in the order currency.

	ERPNext keeps advance_paid in the customer's account currency, which is the company
	currency when the order is in a foreign currency."""
	paid = flt(order.advance_paid)
	account_currency = order.get("party_account_currency") or order.currency
	if account_currency != order.currency:
		return flt((flt(order.base_grand_total) - paid) / (flt(order.conversion_rate) or 1), 2)
	return flt(flt(order.grand_total) - paid, 2)


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
	lines = [line.strip() for line in (account.account_details or "").splitlines() if line.strip()]
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
	"""When a payment pays off a store order: email the customer once and hand the order on."""
	for row in doc.references or []:
		if row.reference_doctype != "Sales Order" or not frappe.db.get_value(
			"Sales Order", row.reference_name, "shop_payment_method"
		):
			continue
		order = frappe.get_doc("Sales Order", row.reference_name)
		if order.docstatus != 1 or amount_due(order) > 0.009:
			continue
		if order.shop_payment_method == totals.BANK:
			order.add_comment("Info", _("Bank transfer received ({0}).").format(doc.name))
			notify_paid(order)
		from shop.fulfillment.service import auto_send

		auto_send(order.name)


def notify_paid(order) -> None:
	if not order.contact_email:
		return
	send_email(
		recipients=[order.contact_email],
		subject=_("Payment received for order {0}").format(order.name),
		message=_("<p>Thank you, we've received your payment for order {0}. We'll let you know when it ships.</p>").format(
			order.name
		),
		reference_doctype="Sales Order",
		reference_name=order.name,
	)


def cancel_overdue_orders() -> None:
	"""Daily: cancel store orders that were never paid by their due date, and tell the customer."""
	if not frappe.get_meta("Sales Order").has_field("shop_payment_method"):
		return
	for name in frappe.get_all(
		"Sales Order",
		filters={
			"docstatus": 1,
			"shop_payment_method": ["in", [totals.BANK, totals.CARD]],
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
	if not order.contact_email:
		return
	store = frappe.get_cached_doc("Shop Settings").store_name or _("our store")
	send_email(
		recipients=[order.contact_email],
		subject=_("Your order {0} has been cancelled").format(order.name),
		message=_(
			"<p>We didn't receive payment for order {0} by {1}, so it has been cancelled.</p>"
			"<p>If you still want the items, please place a new order at {2}. If you have paid, reply to this "
			"email and we'll sort it out.</p>"
		).format(order.name, frappe.utils.formatdate(order.shop_payment_due), frappe.utils.escape_html(store)),
		reference_doctype="Sales Order",
		reference_name=order.name,
	)


def send_email(**kwargs) -> None:
	"""Customer emails never stop an order from being paid or cancelled."""
	try:
		frappe.sendmail(**kwargs)
	except Exception:
		frappe.log_error(title=f"Store email failed: {kwargs.get('subject')}")
