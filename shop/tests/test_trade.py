import frappe
from frappe.tests import IntegrationTestCase

from shop.api import admin, customers, trade


class TestTrade(IntegrationTestCase):
	def setUp(self):
		self.group = frappe.db.get_value("Customer Group", {"is_group": 0})
		previous = frappe.db.get_single_value("Shop Settings", "trade_customer_group")
		self.addCleanup(self.set_group, previous)
		self.set_group(self.group)
		self.customer = frappe.get_doc(
			{"doctype": "Customer", "customer_name": "Trade Test Customer", "customer_group": self.group}
		).insert(ignore_permissions=True)

	def set_group(self, group):
		frappe.db.set_single_value("Shop Settings", "trade_customer_group", group)
		frappe.clear_cache(doctype="Shop Settings")

	def test_trade_discounts_are_pricing_rules_for_the_group(self):
		item_group = frappe.db.get_value("Item Group", {"is_group": 0})
		rules = trade.set_discount(item_group, 12)
		rule = next(row for row in rules if row.item_group == item_group)
		self.assertEqual(rule.discount_percentage, 12)
		self.assertEqual(frappe.db.get_value("Pricing Rule", rule.name, "customer_group"), self.group)
		self.assertEqual(
			[
				row.discount_percentage
				for row in trade.set_discount(item_group, 15)
				if row.item_group == item_group
			],
			[15],
		)
		self.assertNotIn(rule.name, [row.name for row in trade.remove_discount(rule.name)])

	def test_staff_make_and_end_a_trade_customer(self):
		terms = (
			frappe.db.get_value("Payment Terms Template", {})
			or frappe.get_doc(
				{
					"doctype": "Payment Terms Template",
					"template_name": "_Test Trade Net 20",
					"terms": [
						{
							"invoice_portion": 100,
							"credit_days_based_on": "Day(s) after invoice date",
							"credit_days": 20,
						}
					],
				}
			)
			.insert(ignore_permissions=True)
			.name
		)
		other = frappe.db.get_value("Customer Group", {"is_group": 0, "name": ["!=", self.group]})
		self.customer.db_set("customer_group", other)
		with self.assertRaises(frappe.ValidationError):
			trade.make_trade_customer(self.customer.name, allow_on_account=1)
		status = trade.make_trade_customer(
			self.customer.name, payment_terms=terms, allow_on_account=1, credit_limit=500
		)
		self.assertTrue(status["is_trade"])
		self.assertEqual((status["payment_terms"], status["credit_limit"]), (terms, 500))
		self.assertEqual(customers.get_customer(self.customer.name)["trade"]["is_trade"], True)
		self.assertFalse(trade.end_trade(self.customer.name)["allow_on_account"])

	def test_waiting_applications_only_with_trade_accounts(self):
		self.assertIsInstance(admin.get_dashboard()["trade_applications"], int)
		self.set_group(None)
		self.assertIsNone(admin.get_dashboard()["trade_applications"])
