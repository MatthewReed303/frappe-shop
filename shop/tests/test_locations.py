"""A store selling from several warehouses: stock, which warehouse sends each line, a shipping
service chosen per parcel at checkout, and a fulfillment per parcel."""

import json
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import flt

from shop.api import fulfillment as fulfillment_api
from shop.fulfillment import service
from shop.storefront import cart, checkout, orders, stock, totals
from shop.tests.utils import second_warehouse, sellable, use_locations

BUYER = {"email": "parcels-buyer@example.com", "full_name": "Parcel Buyer"}
ADDRESS = {
	"address_line1": "4 Warehouse Way",
	"city": "Bengaluru",
	"state": "Karnataka",
	"country": "India",
	"pincode": "560001",
}
SMALL, BIG = "SHOP-DEMO-003", "SHOP-DEMO-004"
RATES = "shop.tests.test_locations.parcel_rates"
original_hooks = frappe.get_hooks


def parcel_rates(context):
	"""A shipping app quoting each parcel from where it is sent."""
	cheap = 7.0 if context.warehouse and "Second" in context.warehouse else 10.0
	return [
		{"id": f"courier:{context.warehouse}", "label": "Courier", "amount": cheap, "tax_included": False},
		{"id": f"express:{context.warehouse}", "label": "Express", "amount": 25.0, "tax_included": False},
	]


def with_rates(hook=None, *args, **kwargs):
	if hook == "shop_shipping_rate_providers":
		return [RATES]
	return original_hooks(hook, *args, **kwargs)


class TestLocations(IntegrationTestCase):
	def setUp(self):
		frappe.db.delete("Shop Cart")
		if hasattr(frappe.local, "request"):
			del frappe.local.request
		self.first = stock.primary_warehouse()
		self.second = second_warehouse(frappe.get_cached_doc("Shop Settings").company)
		use_locations(
			self,
			{"warehouse": self.first, "fulfillment_provider": "manual", "auto_send": 1},
			{"warehouse": self.second, "fulfillment_provider": "manual", "auto_send": 1},
		)
		sellable(SMALL, 5, self.first)
		# more of BIG than the first warehouse has: the whole line comes from the second
		self.big_qty = max(stock.warehouse_stock([BIG], sellable=True)[BIG][self.first], 0) + 1
		sellable(BIG, self.big_qty, self.second)

	def basket(self):
		cart.add_item(SMALL, qty=1)
		cart.add_item(BIG, qty=self.big_qty)
		return cart.resolve_cart()

	def test_stock_is_the_store_warehouses_together(self):
		by_warehouse = stock.warehouse_stock([BIG], sellable=True)[BIG]
		self.assertEqual(set(by_warehouse), {self.first, self.second})
		self.assertEqual(
			stock.get_stock([BIG], sellable=True)[BIG], sum(max(qty, 0) for qty in by_warehouse.values())
		)

	def test_a_line_comes_from_the_first_warehouse_with_all_of_it(self):
		lines = stock.allocate([(SMALL, 1), (BIG, self.big_qty)])
		self.assertEqual(
			[(line.item_code, line.warehouse) for line in lines], [(SMALL, self.first), (BIG, self.second)]
		)
		# more than either has alone: split, the first warehouse first
		both = stock.warehouse_stock([BIG], sellable=True)[BIG]
		total = max(both[self.first], 0) + both[self.second]
		split = stock.allocate([(BIG, total)])
		self.assertEqual(sum(line.qty for line in split), total)
		self.assertEqual([line.warehouse for line in split][-1], self.second)

	def test_each_parcel_is_priced_on_its_own_and_the_customer_picks_for_each(self):
		from shop.tests.utils import service

		company = frappe.get_cached_doc("Shop Settings").company
		previous = frappe.db.get_single_value("Shop Settings", "shipping_item")
		frappe.db.set_single_value("Shop Settings", "shipping_item", service("TEST-SHIPPING", company))
		frappe.clear_cache(doctype="Shop Settings")
		self.addCleanup(frappe.clear_cache, doctype="Shop Settings")
		self.addCleanup(frappe.db.set_single_value, "Shop Settings", "shipping_item", previous)
		with patch("frappe.get_hooks", side_effect=with_rates):
			basket = self.basket()
			choice = json.dumps({self.first: f"express:{self.first}", self.second: f"courier:{self.second}"})
			quote = totals.compute(basket, ADDRESS, choice)
			shown = totals.payload(quote)
		self.assertEqual(
			[row.id for row in quote.shippings], [f"express:{self.first}", f"courier:{self.second}"]
		)
		self.assertEqual(len(shown["shipping_groups"]), 2)
		self.assertEqual(shown["shipping_groups"][0]["label"], "Parcel 1 of 2")
		self.assertTrue(shown["shipping_groups"][1]["options"][0]["selected"])
		lines = [row for row in totals.summary_lines(quote.order)[0] if row["kind"] == "shipping"]
		self.assertEqual(sum(row["amount"] for row in lines), 32.0)

	def test_an_order_is_sent_and_delivered_parcel_by_parcel(self):
		from shop.storefront import payment

		with patch("frappe.get_hooks", side_effect=with_rates):
			self.basket()
			choice = json.dumps({self.first: f"courier:{self.first}", self.second: f"express:{self.second}"})
			name = checkout.place_order(customer=BUYER, address=ADDRESS, shipping_option=choice)[
				"sales_order"
			]
		order = frappe.get_doc("Sales Order", name)
		self.assertEqual(
			{
				(row.item_code, row.warehouse)
				for row in order.items
				if not totals.is_charge_item(row.item_code)
			},
			{(SMALL, self.first), (BIG, self.second)},
		)
		chosen = json.loads(order.shop_shipping_option)
		self.assertEqual(
			[(row["warehouse"], row["id"]) for row in chosen],
			[(self.first, f"courier:{self.first}"), (self.second, f"express:{self.second}")],
		)

		# paid: each warehouse's parcel goes to its provider, with the customer's choice for it
		service.send_paid_order(name)
		parcels = {row["warehouse"]: row for row in service.order_parcels(name)}
		self.assertEqual(set(parcels), {self.first, self.second})
		for warehouse, option in ((self.first, "courier"), (self.second, "express")):
			sent = parcels[warehouse]["fulfillment"]
			self.assertEqual(
				(sent["warehouse"], sent["provider"], sent["status"]), (warehouse, "manual", "Accepted")
			)
			self.assertEqual(sent["shipping_option"]["id"], f"{option}:{warehouse}")
		self.assertEqual(orders.display_status(order, True), "Preparing your order")

		# the first parcel ships: its goods are delivered, the order is partly shipped
		fulfillment_api.mark_shipped(parcels[self.first]["fulfillment"]["name"], "Courier", "TRACK-1")
		notes = frappe.get_all(
			"Delivery Note Item",
			filters={"against_sales_order": name, "docstatus": 1},
			fields=["item_code", "warehouse"],
		)
		self.assertEqual([(row.item_code, row.warehouse) for row in notes], [(SMALL, self.first)])
		summary = orders.shipment_summary(name)
		self.assertTrue(summary.partly_shipped)
		self.assertEqual(len(summary.parcels), 2)
		self.assertEqual(orders.display_status(frappe.get_doc("Sales Order", name), True), "Partly shipped")
		with self.assertRaises(frappe.ValidationError):
			payment.cancel_by_store(name)

		fulfillment_api.mark_shipped(parcels[self.second]["fulfillment"]["name"], "Courier", "TRACK-2")
		summary = orders.shipment_summary(name)
		self.assertEqual((summary.status, summary.partly_shipped), ("Shipped", False))
		self.assertEqual(flt(frappe.db.get_value("Sales Order", name, "per_delivered")), 100)

	def test_a_parcel_is_sent_once_and_the_order_is_then_with_its_providers(self):
		with patch("frappe.get_hooks", side_effect=with_rates):
			self.basket()
			name = checkout.place_order(customer=BUYER, address=ADDRESS)["sales_order"]
		first = service.send(name)  # the first parcel not sent
		self.assertEqual(frappe.db.get_value("Shop Fulfillment", first, "warehouse"), self.first)
		with self.assertRaises(frappe.ValidationError):
			service.send(name, warehouse=self.first)
		second = service.send(name)
		self.assertEqual(frappe.db.get_value("Shop Fulfillment", second, "warehouse"), self.second)
		with self.assertRaises(frappe.ValidationError):
			service.send(name)

	def test_the_stores_one_warehouse_and_provider_become_its_first_location(self):
		from shop.patches import move_warehouse_to_locations

		use_locations(self)
		for field, value in (
			("default_warehouse", self.first),
			("fulfillment_provider", "manual"),
			("auto_send_to_fulfillment", "1"),
		):
			frappe.db.sql(
				"insert into `tabSingles` (doctype, field, value) values ('Shop Settings', %s, %s)",
				(field, value),
			)
		move_warehouse_to_locations.execute()
		rows = frappe.get_doc("Shop Settings").fulfillment_locations
		self.assertEqual(
			[(row.warehouse, row.fulfillment_provider, row.auto_send) for row in rows],
			[(self.first, "manual", 1)],
		)
		self.assertFalse(
			frappe.db.exists("Singles", {"doctype": "Shop Settings", "field": "default_warehouse"})
		)

	def test_the_customer_is_told_when_part_of_the_order_has_shipped(self):
		from shop.storefront import emails

		emails.ensure_templates()
		order = frappe._dict(doctype="Sales Order", name="SO-PARCELS")
		_subject, partly = emails.render("order_shipped", order, partly=True, order_url="/x")
		_subject, whole = emails.render("order_shipped", order, partly=False, order_url="/x")
		self.assertIn("Part of your order SO-PARCELS has shipped", partly)
		self.assertIn("your order SO-PARCELS has shipped", whole)

	def test_staff_are_warned_when_a_location_cant_send_from_its_address(self):
		from shop.fulfillment.providers.manual import ManualProvider

		with (
			patch.object(ManualProvider, "needs_ship_from", True, create=True),
			patch("frappe.msgprint") as warned,
		):
			settings = frappe.get_doc("Shop Settings")
			settings.fulfillment_locations[1].ship_from_address = None
			settings.validate_fulfillment_locations()
		self.assertTrue(warned.called)
		self.assertIn(self.second, warned.call_args.args[0])
