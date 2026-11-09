"""Shipping surcharges: an amount added to each parcel's delivery price for addresses that match, such
as a rural delivery charge."""

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from shop.api import settings as settings_api
from shop.shop.doctype.shop_shipping_surcharge.shop_shipping_surcharge import address_pattern
from shop.storefront import cart, shipping, stock, totals
from shop.tests.utils import sellable, service, template, use_locations

ITEM = "SHOP-DEMO-003"
RURAL = {"label": "Rural delivery", "address_contains": "RD #", "amount": 6.9, "amount_includes_tax": 1}
original_hooks = frappe.get_hooks


def carrier_rates(context):
	"""Two carriers: one whose prices include the store's tax, one whose don't."""
	return [
		{
			"id": "post:parcel",
			"label": "Post Parcel",
			"amount": 11.5,
			"tax_included": True,
			"carrier": "NZ Post",
		},
		{
			"id": "other:parcel",
			"label": "Other Parcel",
			"amount": 10.0,
			"tax_included": False,
			"carrier": "Other Co",
		},
	]


def with_carriers(hook=None, *args, **kwargs):
	if hook == "shop_shipping_rate_providers":
		return ["shop.tests.test_shipping_surcharges.carrier_rates"]
	return original_hooks(hook, *args, **kwargs)


class TestShippingSurcharges(IntegrationTestCase):
	def setUp(self):
		frappe.db.delete("Shop Cart")
		if hasattr(frappe.local, "request"):
			del frappe.local.request
		settings = frappe.get_doc("Shop Settings")
		fields = (
			"flat_shipping_rate",
			"free_shipping_above",
			"shipping_item",
			"prices_include_tax",
			"tax_template",
		)
		self.addCleanup(self.restore, {field: settings.get(field) for field in fields})
		company = settings.company
		self.home = frappe.get_cached_value("Company", company, "country")
		self.other = "United States" if self.home != "United States" else "Australia"
		settings.update(
			{
				"flat_shipping_rate": 10,
				"free_shipping_above": 0,
				"shipping_item": service("TEST-SHIPPING", company),
				"prices_include_tax": 1,
				"tax_template": template(company, "Test VAT 15 included", 15, 1),
			}
		)
		settings.shipping_surcharges = []
		settings.save(ignore_permissions=True)
		frappe.clear_cache(doctype="Shop Settings")
		use_locations(self, {"warehouse": stock.primary_warehouse(), "fulfillment_provider": "manual"})
		sellable(ITEM, 5)
		cart.add_item(ITEM, qty=1)
		self.cart = cart.resolve_cart()
		self.rural = {
			"address_line1": "1640 Kaipara Coast Highway",
			"address_line2": "RD 4",
			"city": "Warkworth",
			"pincode": "0984",
			"country": self.home,
		}
		self.town = {
			"address_line1": "8 Stuart Street",
			"city": "Dunedin",
			"pincode": "9016",
			"country": self.home,
		}
		self.abroad = {
			"address_line1": "Route 4",
			"address_line2": "RD 7",
			"city": "Town",
			"pincode": "10118",
			"country": self.other,
		}

	def restore(self, values):
		settings = frappe.get_doc("Shop Settings")
		settings.update(values)
		settings.shipping_surcharges = []
		settings.save(ignore_permissions=True)
		frappe.clear_cache(doctype="Shop Settings")

	def surcharge(self, **row):
		settings = frappe.get_doc("Shop Settings")
		settings.append("shipping_surcharges", RURAL | row)
		settings.save(ignore_permissions=True)
		frappe.clear_cache(doctype="Shop Settings")

	def options(self, address):
		found = shipping.get_options(self.cart, frappe._dict(address), 100, refresh=True)["options"]
		return {option["id"]: option for option in found}

	def test_rd_and_a_number_anywhere_in_the_address(self):
		pattern = address_pattern("RD #")
		for text in ("RD 4", "rd4", "R.D. 12", "1640 Kaipara Coast Highway, RD 4, Warkworth", "Rd 2 Waikari"):
			self.assertTrue(pattern.search(text), text)
		for text in ("3rd 4 Street", "Ford 4", "RD Street", "Third Road", "RDX 4"):
			self.assertFalse(pattern.search(text), text)

	def test_a_matching_address_pays_the_surcharge_on_its_delivery_price(self):
		self.surcharge()
		rural = self.options(self.rural)["flat"]
		self.assertAlmostEqual(rural["amount"], 16.9)
		self.assertEqual(rural["label"], "Standard shipping (incl. Rural delivery)")
		self.assertEqual(self.options(self.town)["flat"]["amount"], 10)

	def test_where_a_surcharge_applies(self):
		self.surcharge()
		self.assertEqual(self.options(self.abroad)["flat"]["amount"], 10)
		self.restore_rules(applies_to="Everywhere")
		self.assertAlmostEqual(self.options(self.abroad)["flat"]["amount"], 16.9)
		self.restore_rules(applies_to="Abroad")
		self.assertEqual(self.options(self.rural)["flat"]["amount"], 10)
		self.assertAlmostEqual(self.options(self.abroad)["flat"]["amount"], 16.9)

	def restore_rules(self, **row):
		settings = frappe.get_doc("Shop Settings")
		settings.shipping_surcharges = []
		settings.append("shipping_surcharges", RURAL | row)
		settings.save(ignore_permissions=True)
		frappe.clear_cache(doctype="Shop Settings")

	def test_a_carriers_surcharge_and_the_tax_in_its_price(self):
		self.surcharge(carrier="nz post")
		with patch("frappe.get_hooks", side_effect=with_carriers):
			options = self.options(self.rural)
		self.assertAlmostEqual(options["post:parcel"]["amount"], 11.5 + 6.9)
		self.assertEqual(options["other:parcel"]["amount"], 10.0)
		self.assertEqual(options["flat"]["amount"], 10)
		# every carrier: a price without tax gets the surcharge without tax
		self.restore_rules()
		with patch("frappe.get_hooks", side_effect=with_carriers):
			options = self.options(self.rural)
		self.assertAlmostEqual(options["other:parcel"]["amount"], 10.0 + 6.0)

	def test_collection_is_not_charged(self):
		use_locations(
			self,
			{"warehouse": stock.primary_warehouse(), "fulfillment_provider": "manual", "allow_pickup": 1},
		)
		self.surcharge()
		parcel = shipping.parcels(totals.compute(self.cart, self.town, None, "bank").order)[0]
		found = shipping.get_options(self.cart, frappe._dict(self.rural), 100, True, parcel)["options"]
		pickup = next(option for option in found if shipping.is_pickup(option))
		self.assertEqual(pickup["amount"], 0)
		self.assertNotIn("surcharges", pickup)

	def test_checkout_charges_the_surcharge_on_the_shipping_line(self):
		self.surcharge()
		rural = totals.compute(self.cart, self.rural, None, "bank")
		town = totals.compute(cart.resolve_cart(), self.town, None, "bank")
		self.assertEqual(rural.shipping["label"], "Standard shipping (incl. Rural delivery)")
		self.assertAlmostEqual(rural.order.grand_total - town.order.grand_total, 6.9, delta=0.01)

	def test_staff_set_surcharges_in_settings(self):
		saved = settings_api.save_settings({"shipping_surcharges": [RURAL | {"carrier": "NZ Post"}]})
		self.assertEqual(
			[(row["label"], row["address_contains"], row["carrier"]) for row in saved["shipping_surcharges"]],
			[("Rural delivery", "RD #", "NZ Post")],
		)
		with self.assertRaises(frappe.ValidationError):
			settings_api.save_settings({"shipping_surcharges": [RURAL | {"address_contains": " , "}]})
