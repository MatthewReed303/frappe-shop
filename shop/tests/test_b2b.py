"""Trade accounts: sign-up with terms, applications and approval, customer prices, paying on
account, and quotes accepted online."""

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import flt

from shop.storefront import accounts, cart, checkout, pricing, quotes, totals

ITEM = "SHOP-DEMO-003"
ADDRESS = {"address_line1": "12 Industrial Way", "city": "Town", "pincode": "3204"}


def ensure(doctype, name, values):
	if not frappe.db.exists(doctype, name):
		frappe.get_doc({"doctype": doctype, **values}).insert(ignore_permissions=True)
	return name


class TestTradeAccounts(IntegrationTestCase):
	def setUp(self):
		frappe.db.delete("Shop Cart")
		if hasattr(frappe.local, "request"):
			del frappe.local.request
		for name in ("shop_session_customer", "shop_customer_rates"):
			if hasattr(frappe.local, name):
				delattr(frappe.local, name)
		self.settings = frappe.get_doc("Shop Settings")
		self.original = {f: self.settings.get(f) for f in ("require_terms", "terms_version", "card_fee_item", "card_fee_percent", "card_fee_fixed")}
		frappe.db.set_single_value("Shop Settings", {"require_terms": 1, "terms_version": "T1"})
		frappe.clear_cache(doctype="Shop Settings")
		self.address = dict(ADDRESS, country=frappe.get_cached_value("Company", self.settings.company, "country"))
		currency = self.settings.currency
		self.retail = flt(frappe.db.get_value("Item Price", {"item_code": ITEM, "price_list": self.settings.price_list}, "price_list_rate"))
		ensure("Price List", "_Test Trade", {"price_list_name": "_Test Trade", "currency": currency, "selling": 1, "enabled": 1})
		if not frappe.db.exists("Item Price", {"item_code": ITEM, "price_list": "_Test Trade"}):
			frappe.get_doc({"doctype": "Item Price", "item_code": ITEM, "price_list": "_Test Trade",
							"price_list_rate": flt(self.retail * 0.8, 2)}).insert(ignore_permissions=True)
		ensure("Customer Group", "_Test Trade Group", {"customer_group_name": "_Test Trade Group",
													  "parent_customer_group": "All Customer Groups", "default_price_list": "_Test Trade"})
		ensure("Payment Terms Template", "_Test 20th", {"template_name": "_Test 20th", "terms": [
			{"invoice_portion": 100, "due_date_based_on": "Day(s) after the end of the invoice month", "credit_days": 20}]})
		self.sendmail = patch("frappe.sendmail")
		self.sendmail.start()
		self.addCleanup(self.sendmail.stop)

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.set_single_value("Shop Settings", self.original)
		frappe.clear_cache(doctype="Shop Settings")

	def register(self, email, name="Trade Tester"):
		if not frappe.db.exists("User", email):
			accounts.register(full_name=name, email=email, accept_terms=1)
		return email

	def approved_trade_customer(self, email, on_account=1):
		self.register(email)
		frappe.set_user(email)
		result = accounts.apply_trade(
			{"company_name": f"Trade Co {email.split('@')[0]}", "contact_name": "Trade Tester", "email": email,
			 "country": self.address["country"], "address_line1": "12 Industrial Way", "city": "Town"},
			accept_terms=1,
		)
		frappe.set_user("Administrator")
		application = frappe.get_doc("Shop Trade Application", result["name"])
		application.customer_group = "_Test Trade Group"
		application.allow_on_account = on_account
		application.payment_terms = "_Test 20th" if on_account else None
		application.save()
		accounts.approve_trade(application.name)
		return frappe.db.get_value("Shop Trade Application", application.name, "customer")

	def test_sign_up_needs_the_terms_and_records_them(self):
		with self.assertRaises(frappe.ValidationError):
			accounts.register(full_name="No Terms", email="no.terms@example.com", accept_terms=0)
		email = self.register("with.terms@example.com", "With Terms")
		self.assertEqual(frappe.db.get_value("User", email, "shop_terms_version"), "T1")
		self.assertEqual(frappe.db.get_value("User", email, "user_type"), "Website User")
		with self.assertRaises(frappe.ValidationError):
			accounts.register(full_name="Again", email=email, accept_terms=1)

	def test_approved_trade_customer_sees_trade_prices(self):
		customer = self.approved_trade_customer("trade.prices@example.com")
		self.assertEqual(frappe.db.get_value("Customer", customer, "customer_group"), "_Test Trade Group")
		self.assertEqual(frappe.db.get_value("Customer", customer, "customer_type"), "Company")
		frappe.set_user("trade.prices@example.com")
		price = pricing.get_price(ITEM)
		self.assertEqual(price["rate"], flt(self.retail * 0.8, 2))
		self.assertEqual(price["retail"], self.retail)
		frappe.set_user("Guest")
		self.assertEqual(pricing.get_price(ITEM)["rate"], self.retail)

	def test_customer_pricing_rule_applies(self):
		customer = self.approved_trade_customer("trade.rule@example.com", on_account=0)
		if not frappe.db.exists("Pricing Rule", {"title": "_Test 10% for trade.rule"}):
			frappe.get_doc({"doctype": "Pricing Rule", "title": "_Test 10% for trade.rule", "apply_on": "Item Code",
							"items": [{"item_code": ITEM}], "applicable_for": "Customer", "customer": customer, "selling": 1,
							"rate_or_discount": "Discount Percentage", "discount_percentage": 10,
							"company": self.settings.company, "currency": self.settings.currency}).insert(ignore_permissions=True)
		frappe.set_user("trade.rule@example.com")
		self.assertEqual(pricing.get_price(ITEM)["rate"], flt(self.retail * 0.8 * 0.9, 2))

	def test_pay_on_account_invoices_the_order(self):
		customer = self.approved_trade_customer("trade.account@example.com")
		frappe.set_user("trade.account@example.com")
		methods = [m["method"] for m in totals.payment_methods()]
		self.assertIn(totals.ACCOUNT, methods)
		cart.add_item(ITEM)
		result = checkout.place_order(customer={"email": "trade.account@example.com", "full_name": "Trade Tester"},
									  address=self.address, payment_method=totals.ACCOUNT, accept_terms=1)
		frappe.set_user("Administrator")
		order = frappe.get_doc("Sales Order", result["sales_order"])
		self.assertEqual(order.customer, customer)
		self.assertEqual(order.items[0].rate, flt(self.retail * 0.8, 2))
		invoice = frappe.db.get_value("Sales Invoice Item", {"sales_order": order.name, "docstatus": 1}, "parent")
		self.assertTrue(invoice)
		self.assertEqual(frappe.db.get_value("Sales Invoice", invoice, "payment_terms_template"), "_Test 20th")

	def test_retail_customers_cannot_pay_on_account(self):
		self.register("retail.only@example.com")
		frappe.set_user("retail.only@example.com")
		self.assertNotIn(totals.ACCOUNT, [m["method"] for m in totals.payment_methods()])

	def test_quote_requested_sent_and_accepted(self):
		settings = frappe.get_doc("Shop Settings")
		banks = [row.as_dict() for row in settings.bank_accounts]
		enabled = settings.enable_bank_transfer
		settings.card_fee_item = None
		settings.enable_bank_transfer = 1
		settings.bank_accounts = []
		settings.append("bank_accounts", {"currency": settings.currency, "account_details": "Acct: 1"})
		settings.save(ignore_permissions=True)

		def restore():
			doc = frappe.get_doc("Shop Settings")
			doc.enable_bank_transfer = enabled
			doc.bank_accounts = []
			for row in banks:
				doc.append("bank_accounts", {k: row[k] for k in ("currency", "label", "conversion_fee_percent", "account_details")})
			doc.save(ignore_permissions=True)

		self.addCleanup(restore)
		frappe.clear_cache(doctype="Shop Settings")
		cart.add_item(ITEM, qty=3)
		result = quotes.request_quote(customer={"email": "quote.buyer@example.com", "full_name": "Quote Buyer"},
									  address=self.address, message="PO 123")
		quotation = frappe.get_doc("Quotation", result["quote"])
		self.assertEqual(quotation.docstatus, 0)
		self.assertEqual(quotation.shop_request_message, "PO 123")
		quotation.submit()
		quotes.send_quote(quotation.name)
		summary = quotes.quote_summary(quotation.name, quotation.shop_access_token)
		self.assertTrue(summary["can_accept"])
		with self.assertRaises(frappe.PermissionError):
			quotes.quote_summary(quotation.name, "wrong-token")
		frappe.set_user("Guest")
		accepted = quotes.accept_quote(quotation.name, quotation.shop_access_token, totals.BANK, accept_terms=1)
		frappe.set_user("Administrator")
		order = frappe.get_doc("Sales Order", accepted["sales_order"])
		self.assertEqual(flt(order.grand_total), flt(quotation.grand_total))
		self.assertEqual(order.shop_payment_method, totals.BANK)
		self.assertIn(order.shop_access_token, accepted["redirect"])
		with self.assertRaises(frappe.ValidationError):
			quotes.accept_quote(quotation.name, quotation.shop_access_token, totals.BANK, accept_terms=1)

	def test_card_fee_is_added_to_quotes_paid_by_card(self):
		total = 100.0
		frappe.db.set_single_value("Shop Settings", {"card_fee_item": "SHOP-DEMO-003", "card_fee_percent": 2.65, "card_fee_fixed": 0.3})
		frappe.clear_cache(doctype="Shop Settings")
		fee = totals.card_fee_for(total, export=False)
		self.assertGreater(fee, 0)
		factor = 1 + (totals.included_tax_rate() / 100 if self.settings.prices_include_tax else 0)
		self.assertAlmostEqual(fee / factor, 0.0265 * (total + fee) + 0.3, delta=0.01)


class TestStoreDiscounts(IntegrationTestCase):
	def setUp(self):
		for name in ("shop_session_customer", "shop_customer_rates"):
			if hasattr(frappe.local, name):
				delattr(frappe.local, name)
		self.parent = ensure("Item Group", "_Test Store Hardware", {"item_group_name": "_Test Store Hardware",
																	"parent_item_group": "All Item Groups", "is_group": 1})
		self.child = ensure("Item Group", "_Test Store HATs", {"item_group_name": "_Test Store HATs",
															   "parent_item_group": "_Test Store Hardware"})
		self.item = "_Test Store HAT Item"
		if not frappe.db.exists("Item", self.item):
			frappe.get_doc({"doctype": "Item", "item_code": self.item, "item_name": self.item, "item_group": self.child,
							"stock_uom": "Nos", "is_stock_item": 0}).insert(ignore_permissions=True)
		settings = frappe.get_doc("Shop Settings")
		if not frappe.db.exists("Item Price", {"item_code": self.item, "price_list": settings.price_list}):
			frappe.get_doc({"doctype": "Item Price", "item_code": self.item, "price_list": settings.price_list,
							"price_list_rate": 100}).insert(ignore_permissions=True)
		self.group = ensure("Customer Group", "_Test Discount Group", {"customer_group_name": "_Test Discount Group",
																	  "parent_customer_group": "All Customer Groups"})
		self.customer = ensure("Customer", "_Test Discount Customer", {"customer_name": "_Test Discount Customer",
																	   "customer_group": "_Test Discount Group"})

	def rate(self):
		frappe.local.shop_customer_rates = {}
		return pricing.customer_rate(self.item, self.customer, frappe.get_cached_doc("Shop Settings").price_list, 1)

	def test_group_discount_reaches_sub_groups_and_customer_overrides_it(self):
		group = frappe.get_doc("Customer Group", self.group)
		group.set("shop_discounts", [{"item_group": self.parent, "discount_percentage": 15}])
		group.save()
		self.assertEqual(self.rate(), 85)
		customer = frappe.get_doc("Customer", self.customer)
		customer.set("shop_discounts", [{"item_group": self.child, "discount_percentage": 25}])
		customer.save()
		self.assertEqual(self.rate(), 75)
		customer.set("shop_discounts", [])
		customer.save()
		self.assertEqual(self.rate(), 85)
		self.assertFalse(frappe.db.exists("Pricing Rule", {"title": f"Store discount: {self.customer} / {self.child}"}))

	def test_same_group_twice_is_refused(self):
		customer = frappe.get_doc("Customer", self.customer)
		customer.set("shop_discounts", [{"item_group": self.child, "discount_percentage": 5},
										{"item_group": self.child, "discount_percentage": 10}])
		with self.assertRaises(frappe.ValidationError):
			customer.save()


class TestStatements(IntegrationTestCase):
	GROUP, OTHER, RUN = "_Test Statement Group", "_Test Other Group", "_Test Statement Run"

	def setUp(self):
		for group in (self.GROUP, self.OTHER):
			ensure("Customer Group", group, {"customer_group_name": group, "parent_customer_group": "All Customer Groups"})
		if not frappe.db.exists("Process Statement Of Accounts", self.RUN):
			run = frappe.new_doc("Process Statement Of Accounts")
			run.update({"__newname": self.RUN, "company": frappe.get_cached_doc("Shop Settings").company,
						"report": "General Ledger", "customer_collection": "Customer Group", "collection_name": self.GROUP,
						"enable_auto_email": 1, "frequency": "Monthly", "primary_mandatory": 1,
						"from_date": "2026-01-01", "to_date": "2026-01-31"})
			run.flags.ignore_validate = run.flags.ignore_mandatory = True
			run.insert(ignore_permissions=True)

	def rows(self):
		return frappe.get_all("Process Statement Of Accounts Customer", filters={"parent": self.RUN}, fields=["customer", "primary_email"])

	def test_store_customer_gets_a_primary_contact_and_email(self):
		customer = accounts.ensure_customer("statement.one@example.com", "Statement One")
		values = frappe.db.get_value("Customer", customer, ["customer_primary_contact", "email_id"], as_dict=True)
		self.assertTrue(values.customer_primary_contact)
		self.assertEqual(values.email_id, "statement.one@example.com")

	def test_customer_joins_and_leaves_the_statement_run_with_their_group(self):
		customer = frappe.get_doc("Customer", accounts.ensure_customer("statement.two@example.com", "Statement Two"))
		customer.customer_group = self.GROUP
		customer.save(ignore_permissions=True)
		self.assertIn({"customer": customer.name, "primary_email": "statement.two@example.com"}, [dict(r) for r in self.rows()])
		customer.customer_group = self.OTHER
		customer.save(ignore_permissions=True)
		self.assertNotIn(customer.name, [r.customer for r in self.rows()])

	def test_statement_needs_a_signed_in_customer(self):
		from shop.storefront import statements

		frappe.set_user("Guest")
		try:
			with self.assertRaises(frappe.PermissionError):
				statements.statement_pdf()
		finally:
			frappe.set_user("Administrator")
