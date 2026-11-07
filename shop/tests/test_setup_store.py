"""Setting a store up on ERPNext's own records, and doing it again changes nothing."""

import frappe
from frappe.tests import IntegrationTestCase

from shop import setup_store


class TestSetUpStore(IntegrationTestCase):
	def setUp(self):
		self.settings = frappe.get_doc("Shop Settings")
		self.original = {f: self.settings.get(f) for f in (
			"shipping_item", "card_fee_item", "conversion_fee_item", "trade_customer_group", "tax_template")}
		self.addCleanup(lambda: frappe.db.set_single_value("Shop Settings", self.original))

	def test_store_is_set_up_on_erpnext_records_and_again_changes_nothing(self):
		settings = setup_store.set_up(
			trade_group="_Test Setup Trade", trade_discounts={"All Item Groups": 10}, statements=True
		)
		company = settings.company
		for field, code, _name, account_name in setup_store.CHARGE_ITEMS:
			self.assertEqual(settings.get(field), code)
			income = frappe.db.get_value("Item Default", {"parent": code, "company": company}, "income_account")
			self.assertEqual(frappe.db.get_value("Account", income, "account_name"), account_name)
		self.assertEqual(settings.trade_customer_group, "_Test Setup Trade")
		group = frappe.get_doc("Customer Group", "_Test Setup Trade")
		self.assertEqual([(r.item_group, r.discount_percentage) for r in group.shop_discounts], [("All Item Groups", 10)])
		if settings.tax_template:
			rules = frappe.get_all("Tax Rule", filters={"company": company, "use_for_shopping_cart": 1}, pluck="sales_tax_template")
			self.assertIn(settings.tax_template, rules)
		run = f"{settings.store_name or frappe.db.get_value('Company', company, 'company_name')} statements"
		self.assertEqual(frappe.db.get_value("Process Statement Of Accounts", run, "collection_name"), "_Test Setup Trade")

		# an owner's change is kept when set-up runs again
		group.shop_discounts[0].discount_percentage = 12
		group.save()
		before = frappe.db.count("Tax Rule")
		setup_store.set_up(trade_group="_Test Setup Trade", trade_discounts={"All Item Groups": 10})
		self.assertEqual(frappe.db.get_value("Shop Discount", {"parent": "_Test Setup Trade"}, "discount_percentage"), 12)
		self.assertEqual(frappe.db.count("Tax Rule"), before)
