import frappe, json

def reset_once():
	print("before", frappe.db.get_value("Coupon Code", "TESTONCE (test)", "used"))
	frappe.db.set_value("Coupon Code", "TESTONCE (test)", "used", 0)
	frappe.db.commit()

def age(name, hours=2):
	from frappe.utils import add_to_date, now_datetime
	frappe.db.set_value("Sales Order", name, "creation", add_to_date(now_datetime(), hours=-int(hours)), update_modified=False)
	frappe.db.commit()
	print(name, frappe.db.get_value("Sales Order", name, ["creation", "shop_payment_due", "advance_payment_status"]))

def gl(voucher):
	for r in frappe.get_all("GL Entry", {"voucher_no": voucher, "is_cancelled": 0}, ["account", "debit", "credit", "debit_in_account_currency", "credit_in_account_currency"]):
		print(voucher, r.account, r.debit, r.credit)

def would_remind():
	from frappe.utils import add_to_date, now_datetime, nowdate
	from shop.storefront import totals
	print(frappe.get_all("Sales Order", filters={"docstatus": 1, "shop_payment_method": totals.GATEWAY, "advance_payment_status": ["!=", "Fully Paid"], "creation": ["<", add_to_date(now_datetime(), hours=-1)], "shop_payment_due": [">=", nowdate()]}, fields=["name", "status", "contact_email", "shop_payment_due"]))

def chain(so):
	"""SO -> PR, PE, SI, DN amounts and GL sums"""
	d = frappe.get_doc("Sales Order", so)
	print("SO", so, d.grand_total, d.status, d.advance_payment_status, d.customer_group, "coupon", d.coupon_code, "addr", d.customer_address, "/", d.shipping_address_name)
	for pr in frappe.get_all("Payment Request", {"reference_name": so}, ["name", "status", "grand_total", "docstatus"]):
		print(" PR", pr)
	pes = frappe.get_all("Payment Entry Reference", {"reference_name": so, "docstatus": 1}, ["parent", "allocated_amount"])
	sis = frappe.get_all("Sales Invoice Item", {"sales_order": so, "docstatus": 1}, pluck="parent", distinct=True)
	for si in sis:
		s = frappe.get_doc("Sales Invoice", si)
		print(" SI", si, s.grand_total, s.outstanding_amount, s.status, s.is_return, "billing", s.customer_address, "due", s.due_date, "coupon", s.coupon_code, [(i.item_code, i.rate, i.amount) for i in s.items])
		pes += frappe.get_all("Payment Entry Reference", {"reference_name": si, "docstatus": 1}, ["parent", "allocated_amount"])
		for r in frappe.get_all("GL Entry", {"voucher_no": si, "is_cancelled": 0}, ["account", "debit", "credit"]):
			print("   GL", r.account, r.debit, r.credit)
	for pe in {p.parent for p in pes}:
		p = frappe.get_doc("Payment Entry", pe)
		print(" PE", pe, p.paid_amount, p.received_amount, p.paid_to, p.reference_no, [(r.reference_doctype, r.reference_name, r.allocated_amount) for r in p.references])
		for r in frappe.get_all("GL Entry", {"voucher_no": pe, "is_cancelled": 0}, ["account", "debit", "credit"]):
			print("   GL", r.account, r.debit, r.credit)
	for dn in frappe.get_all("Delivery Note Item", {"against_sales_order": so, "docstatus": 1}, pluck="parent", distinct=True):
		print(" DN", dn, frappe.db.get_value("Delivery Note", dn, ["status", "is_return", "grand_total"]))
	for c in frappe.get_all("Comment", {"reference_name": so, "comment_type": "Info"}, ["content"]):
		print(" COMMENT", c.content[:150])

def coupon_probe(code="TEST20OFF (test)", item="TEST-TEMP-PT100", qty=5, customer=None):
	from shop.storefront import totals
	from shop.storefront.checkout import elevated
	class C: pass
	cart = C(); cart.coupon_code = code; cart.items = [frappe._dict(item_code=item, qty=float(qty))]; cart.get = lambda k, d=None: getattr(cart, k, d)
	with elevated():
		order = totals.new_order(cart, customer, "NZD", None)
		print("after new_order: total", order.total, "net", order.net_total, "discount", order.discount_amount, "coupon", order.coupon_code)
		totals.calculate(order)
		print("after calculate: total", order.total, "discount", order.discount_amount, "applied", totals.apply_coupon(order))
	frappe.db.rollback()
