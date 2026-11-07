"""Setting a store up on ERPNext's own records, and doing it again changes nothing."""

import frappe
from frappe.tests import IntegrationTestCase

from shop import setup_store


class TestSetUpStore(IntegrationTestCase):
	def setUp(self):
		self.settings = frappe.get_doc("Shop Settings")
		self.original = {f: self.settings.get(f) for f in (
			"shipping_item", "card_fee_item", "conversion_fee_item", "trade_customer_group", "tax_template", "export_tax_template")}
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
		rule = frappe.db.get_value("Pricing Rule", {"customer_group": "_Test Setup Trade"}, ["name", "discount_percentage"], as_dict=True)
		self.assertEqual(rule.discount_percentage, 10)
		if settings.tax_template:
			self.assertTrue(settings.export_tax_template)
		run = f"{settings.store_name or frappe.db.get_value('Company', company, 'company_name')} statements"
		self.assertEqual(frappe.db.get_value("Process Statement Of Accounts", run, "collection_name"), "_Test Setup Trade")

		# an owner's changes are kept when set-up runs again
		frappe.db.set_value("Pricing Rule", rule.name, "discount_percentage", 12)
		frappe.db.set_single_value("Shop Settings", "shipping_item", "SHOP-DEMO-003")
		frappe.clear_cache(doctype="Shop Settings")
		rules = frappe.db.count("Pricing Rule")
		setup_store.set_up(trade_group="_Test Setup Trade", trade_discounts={"All Item Groups": 10})
		self.assertEqual(frappe.db.get_value("Pricing Rule", rule.name, "discount_percentage"), 12)
		self.assertEqual(frappe.db.count("Pricing Rule"), rules)
		self.assertEqual(frappe.db.get_single_value("Shop Settings", "shipping_item"), "SHOP-DEMO-003")
