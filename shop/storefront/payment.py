"""How an order is paid: bank transfer instructions, payment status and overdue orders."""

import json
import re

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
			{
				"reference_doctype": "Sales Order",
				"reference_name": order.name,
				"status": "Paid",
				"docstatus": 1,
			},
		)
	)


def payment_status(order) -> str:
	"""Paid, Part paid or Unpaid: from what ERPNext records on the order, and for an order paid on
	account, from its invoices."""
	if is_paid(order) or invoices_paid(order.name):
		return "Paid"
	if flt(order.advance_paid) > 0:
		return "Part paid"
	return "Unpaid"


def invoices_paid(order_name: str) -> bool:
	invoices = frappe.get_all(
		"Sales Invoice Item",
		filters={"sales_order": order_name, "docstatus": 1},
		pluck="parent",
		distinct=True,
	)
	if not invoices:
		return False
	return not frappe.get_all(
		"Sales Invoice",
		filters={"name": ["in", invoices], "is_return": 0, "outstanding_amount": [">", 0]},
		limit=1,
	)


def method(order) -> str | None:
	return order.get("shop_payment_method") or None


def bank_instructions(order) -> dict | None:
	"""What to pay, where, and with which reference, for a bank transfer order."""
	if method(order) not in totals.BANK_METHODS:
		return None
	account = totals.bank_account(order.currency, method(order))
	if not account:
		return None
	lines = account.details
	return {
		"currency": order.currency,
		"label": account.label or order.currency,
		"amount": totals.money(amount_due(order), order.currency),
		"reference": order.name,
		"details": lines,
		"due": frappe.utils.formatdate(order.get("shop_payment_due"))
		if order.get("shop_payment_due")
		else "",
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
		if order.shop_payment_method in totals.BANK_METHODS:
			order.add_comment("Info", _("Bank transfer received ({0}).").format(doc.name))
			notify_paid(order)


def on_bank_transaction(doc, method=None) -> None:
	"""A deposit into one of the company's bank accounts may pay a bank transfer order; it is matched
	once the Bank Transaction is saved."""
	if flt(doc.deposit) > 0:
		frappe.enqueue(
			"shop.storefront.payment.pay_from_bank_transaction",
			bank_transaction=doc.name,
			enqueue_after_commit=True,
		)


def pay_from_bank_transaction(bank_transaction: str) -> str | None:
	"""Pay the bank transfer order a deposit names in its reference: a Payment Entry for what the
	customer sent, with the bank's fee as a charge, reconciled with the Bank Transaction. A payment
	short of what is owed leaves the order waiting and tells the customer the balance."""
	from erpnext.accounts.doctype.bank_reconciliation_tool.bank_reconciliation_tool import reconcile_vouchers

	from shop.storefront import emails
	from shop.storefront.checkout import elevated

	transaction = frappe.get_doc("Bank Transaction", bank_transaction)
	if transaction.docstatus != 1 or flt(transaction.unallocated_amount) <= 0:
		return None
	order = order_paid_by(transaction)
	if not order:
		return None
	with elevated():
		payment_entry = make_payment_entry(order, transaction)
		reconcile_vouchers(
			transaction.name,
			json.dumps(
				[
					{
						"payment_doctype": "Payment Entry",
						"payment_name": payment_entry.name,
						"amount": flt(transaction.unallocated_amount),
					}
				]
			),
		)
		order.reload()
		owed = amount_due(order)
		if owed > 0:
			received = money_text(flt(payment_entry.received_amount), order.currency)
			order.add_comment(
				"Info",
				_("Bank transfer of {0} received ({1}); {2} is still owed.").format(
					received, payment_entry.name, money_text(owed, order.currency)
				),
			)
			emails.send(
				"payment_short",
				order.contact_email,
				order,
				received=received,
				owed=money_text(owed, order.currency),
				payment_html=bank_instructions_html(order),
			)
	return payment_entry.name


def order_paid_by(transaction):
	"""The one unpaid bank transfer order, in the deposit's currency, whose number is in the
	deposit's reference or description."""
	text = f"{transaction.reference_number or ''} {transaction.description or ''}"
	names = frappe.get_all(
		"Sales Order",
		filters={
			"docstatus": 1,
			"company": transaction.company,
			"currency": transaction.currency,
			"shop_payment_method": ["in", list(totals.BANK_METHODS)],
			"advance_payment_status": ["!=", "Fully Paid"],
			"status": ["not in", ["Closed", "Completed", "On Hold"]],
		},
		pluck="name",
	)
	matched = [name for name in names if re.search(rf"\b{re.escape(name)}\b", text, re.IGNORECASE)]
	return frappe.get_doc("Sales Order", matched[0]) if len(matched) == 1 else None


def make_payment_entry(order, transaction):
	"""ERPNext's Payment Entry for the order, for what the customer sent: the deposit plus the fee the
	bank took, which is booked to the store's Bank Charges Account."""
	account = frappe.db.get_value("Bank Account", transaction.bank_account, "account")
	charges_account = frappe.get_cached_doc("Shop Settings").get("bank_charges_account")
	fee = flt(transaction.included_fee) if charges_account else 0
	payment_entry, rate = payment_entry_for(order, account, flt(transaction.unallocated_amount) + fee)
	if fee:
		payment_entry.append(
			"taxes",
			{
				"charge_type": "Actual",
				"add_deduct_tax": "Deduct",
				"account_head": charges_account,
				"description": _("Bank fee"),
				"tax_amount": flt(fee * rate, payment_entry.precision("paid_amount")),
				"cost_center": frappe.get_cached_value("Company", order.company, "cost_center"),
			},
		)
	payment_entry.bank_account = transaction.bank_account
	payment_entry.reference_no = transaction.transaction_id or transaction.name
	payment_entry.reference_date = transaction.date
	payment_entry.insert()
	payment_entry.submit()
	return payment_entry


def payment_entry_for(order, account: str | None, received: float | None = None):
	"""ERPNext's Payment Entry for the order, for what was received into the bank account (all that
	is owed when not given), and the rate from the bank's currency to the customer's account."""
	from erpnext.accounts.doctype.payment_entry.payment_entry import get_payment_entry

	payment_entry = get_payment_entry("Sales Order", order.name, bank_account=account, bank_amount=received)
	rate = flt(payment_entry.target_exchange_rate) / flt(payment_entry.source_exchange_rate)
	if received and received < amount_due(order):
		payment_entry = get_payment_entry(
			"Sales Order",
			order.name,
			bank_account=account,
			bank_amount=received,
			party_amount=flt(received * rate, payment_entry.precision("paid_amount")),
		)
	return payment_entry, rate


def money_text(amount: float, currency: str) -> str:
	return f"{totals.money(amount, currency)} {currency}"


def was_paid_before(order, reference) -> bool:
	"""Whether the order was already paid in full before this payment."""
	paid, allocated = flt(order.advance_paid), flt(reference.allocated_amount)
	if (order.get("party_account_currency") or order.currency) != order.currency:
		paid, allocated = (
			paid / (flt(order.conversion_rate) or 1),
			allocated / (flt(order.conversion_rate) or 1),
		)
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
	emails.send(
		"payment_received",
		order.contact_email,
		order,
		attachments=invoice_attachments(invoice),
		invoice=invoice,
	)


def order_invoice(order_name: str) -> str | None:
	"""The submitted Sales Invoice made from this order, if there is one."""
	return frappe.db.get_value("Sales Invoice Item", {"sales_order": order_name, "docstatus": 1}, "parent")


def invoice_paid_order(order) -> str | None:
	"""Invoice a store order once it is paid in full, with the payment set against the invoice.

	ERPNext's own Sales Invoice is made from the Sales Order, and its Allocate Advances
	Automatically option takes the payment already recorded against the order. Orders paid on
	account are invoiced when they are placed. A failure is logged and never blocks the payment."""
	if order.get("shop_payment_method") not in (totals.GATEWAY, *totals.BANK_METHODS, totals.COD):
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
		order.add_comment(
			"Info",
			_("The order is paid but could not be invoiced automatically. Make the invoice from the order."),
		)
		return None
	order.add_comment("Info", _("Paid and invoiced: {0}.").format(invoice.name))
	return invoice.name


def invoice_attachments(invoice: str | None) -> list:
	if not invoice:
		return []
	from shop.storefront.checkout import elevated

	try:
		# the invoice goes to its own customer, also from a payment confirmed while signed out
		with elevated():
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
			"shop_payment_method": ["in", [*totals.BANK_METHODS, totals.GATEWAY]],
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
	unmatched = unmatched_payments(order)
	if unmatched:
		# the customer may have paid without the payment being matched to the order
		order.add_comment(
			"Info",
			_(
				"Not cancelled: {0} has payments not matched to an order yet ({1}). Match them in ERPNext's Payment Reconciliation."
			).format(order.customer, ", ".join(unmatched)),
		)
		return False
	for request in frappe.get_all(
		"Payment Request",
		filters={
			"reference_doctype": "Sales Order",
			"reference_name": name,
			"docstatus": 1,
			"status": ["!=", "Paid"],
		},
		pluck="name",
	):
		frappe.get_doc("Payment Request", request).cancel()
	# cancelling a Payment Request updates the order
	order.reload()
	order.flags.ignore_permissions = True
	order.cancel()
	order.add_comment("Info", _("Cancelled automatically: not paid by {0}.").format(order.shop_payment_due))
	notify_cancelled(order)
	return True


def unmatched_payments(order) -> list[str]:
	"""The customer's Payment Entries since the order with money not allocated to anything."""
	return frappe.get_all(
		"Payment Entry",
		filters={
			"party_type": "Customer",
			"party": order.customer,
			"docstatus": 1,
			"payment_type": "Receive",
			"unallocated_amount": [">", 0],
			"posting_date": [">=", order.transaction_date],
		},
		pluck="name",
	)


REMINDER = "Payment reminder sent."


def remind_unpaid_online_orders() -> None:
	"""Remind once a customer who left the payment page: an hour after an online order was placed."""
	from frappe.utils import add_to_date, now_datetime

	for name in frappe.get_all(
		"Sales Order",
		filters={
			"docstatus": 1,
			"shop_payment_method": totals.GATEWAY,
			"advance_payment_status": ["!=", "Fully Paid"],
			"creation": ["<", add_to_date(now_datetime(), hours=-1)],
			"shop_payment_due": [">=", nowdate()],
		},
		pluck="name",
	):
		if frappe.db.exists(
			"Comment", {"reference_doctype": "Sales Order", "reference_name": name, "content": REMINDER}
		):
			continue
		order = frappe.get_doc("Sales Order", name)
		if not is_paid(order):
			remind(order)


def remind(order) -> None:
	from shop.storefront import emails
	from shop.storefront.checkout import order_email_parts
	from shop.storefront.orders import order_url, pay_url

	url = pay_url(order)
	if not url:
		return
	parts = order_email_parts(order, order_url(order.name))
	emails.send(
		"payment_reminder",
		order.contact_email,
		order,
		pay_url=url,
		due_date=frappe.utils.formatdate(order.shop_payment_due) if order.get("shop_payment_due") else "",
		**{key: parts[key] for key in ("lines_html", "order_url") if key in parts},
	)
	order.add_comment("Info", REMINDER)


def notify_cancelled(order) -> None:
	from shop.storefront import emails

	emails.send(
		"order_cancelled",
		order.contact_email,
		order,
		due_date=frappe.utils.formatdate(order.shop_payment_due),
	)
