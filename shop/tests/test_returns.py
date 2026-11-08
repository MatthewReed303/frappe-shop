from unittest.mock import MagicMock, patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import flt

from shop.api import fulfillment as fulfillment_api
from shop.api import returns as returns_api
from shop.fulfillment import service
from shop.storefront import cart, checkout, returns

BUYER = {"email": "returns-buyer@example.com", "full_name": "Returns Buyer"}
ADDRESS = {
	"address_line1": "9 Return Road",
	"city": "Bengaluru",
	"state": "Karnataka",
	"country": "India",
	"pincode": "560001",
}


class TestReturns(IntegrationTestCase):
	def setUp(self):
		frappe.db.delete("Shop Cart")
		if hasattr(frappe.local, "request"):
			del frappe.local.request

	def shipped_order(self):
		cart.add_item("SHOP-DEMO-003")
		order = checkout.place_order(customer=BUYER, address=ADDRESS)["sales_order"]
		token = frappe.db.get_value("Shop Cart", {"sales_order": order}, "token")
		name = service.send(order, "manual")
		fulfillment_api.mark_shipped(name, carrier="Delhivery", tracking_number="RTN-1")
		return order, token

	def test_guest_requests_a_return_with_the_order_token(self):
		order, token = self.shipped_order()
		result = returns.create_request(
			order=order, item_code="SHOP-DEMO-003", request_type="Return", reason="Wrong size", token=token
		)
		self.assertEqual(result["status"], "Requested")
		request = frappe.get_doc("Shop Return Request", result["name"])
		self.assertEqual(request.item_name, "Ceramic Mug")
		self.assertEqual(request.customer, frappe.db.get_value("Sales Order", order, "customer"))

	def test_wrong_token_is_rejected(self):
		order, _token = self.shipped_order()
		with self.assertRaises(frappe.PermissionError):
			returns.create_request(
				order=order, item_code="SHOP-DEMO-003", request_type="Return", reason="x", token="wrong"
			)

	def test_the_return_window_is_the_stores_setting(self):
		days = frappe.db.get_single_value("Shop Settings", "return_window_days")
		self.addCleanup(frappe.db.set_single_value, "Shop Settings", "return_window_days", days)
		frappe.db.set_single_value("Shop Settings", "return_window_days", 30)
		frappe.clear_cache(doctype="Shop Settings")
		self.assertEqual(returns.returns_note(), "30-day returns")
		frappe.db.set_single_value("Shop Settings", "return_window_days", 0)
		frappe.clear_cache(doctype="Shop Settings")
		self.assertIsNone(returns.returns_note())

	def test_unshipped_orders_are_not_returnable(self):
		cart.add_item("SHOP-DEMO-004")
		order = checkout.place_order(customer=BUYER, address=ADDRESS)["sales_order"]
		token = frappe.db.get_value("Shop Cart", {"sales_order": order}, "token")
		with self.assertRaises(frappe.ValidationError):
			returns.create_request(
				order=order, item_code="SHOP-DEMO-004", request_type="Return", reason="x", token=token
			)

	def test_an_order_handed_to_a_courier_is_returnable_once_it_ships(self):
		cart.add_item("SHOP-DEMO-004")
		order = checkout.place_order(customer=BUYER, address=ADDRESS)["sales_order"]
		token = frappe.db.get_value("Shop Cart", {"sales_order": order}, "token")
		name = service.send(order, "manual")
		# booked with the courier (the stock may already be delivered in ERPNext), not picked up yet
		frappe.db.set_value("Shop Fulfillment", name, "status", "Accepted")
		self.assertFalse(returns.summary(order)["eligible"])
		with self.assertRaisesRegex(frappe.ValidationError, "once the order has shipped"):
			returns.create_request(
				order=order, item_code="SHOP-DEMO-004", request_type="Return", reason="x", token=token
			)
		fulfillment_api.mark_shipped(name, carrier="Delhivery", tracking_number="RTN-2")
		self.assertTrue(returns.summary(order)["eligible"])

	def test_a_cancelled_shipment_is_not_returnable(self):
		cart.add_item("SHOP-DEMO-004")
		order = checkout.place_order(customer=BUYER, address=ADDRESS)["sales_order"]
		name = service.send(order, "manual")
		service.cancel(name)
		self.assertFalse(returns.summary(order)["eligible"])

	def test_one_open_request_per_item(self):
		order, token = self.shipped_order()
		returns.create_request(
			order=order, item_code="SHOP-DEMO-003", request_type="Return", reason="Wrong size", token=token
		)
		with self.assertRaises(frappe.ValidationError):
			returns.create_request(
				order=order,
				item_code="SHOP-DEMO-003",
				request_type="Replacement",
				reason="Again",
				token=token,
			)

	def test_items_outside_the_order_are_rejected(self):
		order, token = self.shipped_order()
		with self.assertRaises(frappe.ValidationError):
			returns.create_request(
				order=order, item_code="SHOP-DEMO-008", request_type="Return", reason="x", token=token
			)

	def test_manager_resolves_the_request(self):
		order, token = self.shipped_order()
		created = returns.create_request(
			order=order, item_code="SHOP-DEMO-003", request_type="Return", reason="Chipped", token=token
		)
		updated = returns_api.set_status(created["name"], "Approved", note="Pickup on Friday")
		self.assertEqual(updated["status"], "Approved")
		summary = returns.summary(order)
		self.assertEqual(summary["requests"][0]["status"], "Approved")
		self.assertEqual(summary["requests"][0]["resolution_note"], "Pickup on Friday")
		listed = returns_api.get_requests(status="Approved")
		self.assertIn(created["name"], [row.name for row in listed["requests"]])

	def test_a_return_on_a_paid_order_never_invoiced_is_still_owed_back(self):
		from shop.api import orders as orders_api

		cart.add_item("SHOP-DEMO-003", qty=1)
		order = checkout.place_order(customer=BUYER, address=ADDRESS, payment_method="bank")["sales_order"]
		token = frappe.db.get_value("Shop Cart", {"sales_order": order}, "token")
		orders_api.mark_paid(order)
		name = service.send(order, "manual")
		fulfillment_api.mark_shipped(name, carrier="Delhivery", tracking_number="RTN-5")
		created = returns.create_request(
			order=order, item_code="SHOP-DEMO-003", request_type="Return", reason="Broken", qty=1, token=token
		)
		returns_api.set_status(created["name"], "Approved")
		with patch("frappe.sendmail"):
			result = returns_api.set_status(created["name"], "Completed")
		self.assertTrue(result["credit_note"])
		self.assertEqual(
			[row["credit_note"] for row in orders_api.get_order(order)["refunds_due"]],
			[result["credit_note"]],
		)

	def test_completed_return_credits_and_staff_approve_the_card_refund(self):
		from shop.api import orders as orders_api
		from shop.storefront import payment

		cart.add_item("SHOP-DEMO-003", qty=2)
		order = checkout.place_order(customer=BUYER, address=ADDRESS)["sales_order"]
		token = frappe.db.get_value("Shop Cart", {"sales_order": order}, "token")
		orders_api.mark_paid(order)
		payment.invoice_paid_order(frappe.get_doc("Sales Order", order))
		name = service.send(order, "manual")
		fulfillment_api.mark_shipped(name, carrier="Delhivery", tracking_number="RTN-3")
		created = returns.create_request(
			order=order, item_code="SHOP-DEMO-003", request_type="Return", reason="Broken", qty=1, token=token
		)
		returns_api.set_status(created["name"], "Approved")

		# a card payment: the order's gateway refunds it
		gateway = MagicMock()
		gateway.refund.return_value = "re_test_refund"
		paid_request = frappe._dict(name="PR-TEST", payment_gateway="Stripe", payment_account=None)
		real_get_doc = frappe.get_doc

		def get_doc(*args, **kwargs):
			if args and args[0] == "Payment Request":
				return paid_request
			if args and args[0] == "Payment Gateway":
				return frappe._dict(gateway_settings="Stripe Settings", gateway_controller="Test")
			if args and args[0] == "Stripe Settings":
				return gateway
			return real_get_doc(*args, **kwargs)

		with patch("frappe.sendmail") as sendmail:
			result = returns_api.set_status(created["name"], "Completed")
		self.assertEqual(frappe.db.get_value("Delivery Note", result["return_delivery_note"], "is_return"), 1)
		self.assertEqual(
			frappe.db.get_value("Delivery Note Item", {"parent": result["return_delivery_note"]}, "qty"), -1
		)
		credit_note = frappe.get_doc("Sales Invoice", result["credit_note"])
		self.assertTrue(credit_note.is_return)
		self.assertEqual(credit_note.items[0].qty, -1)
		subjects = [call.kwargs["subject"] for call in sendmail.call_args_list]
		self.assertIn(f"Your return for order {order} is complete", subjects)
		# nothing is paid back until staff approve the refund
		self.assertFalse(result["refund"])
		gateway.refund.assert_not_called()
		self.assertEqual(
			[row["credit_note"] for row in orders_api.get_order(order)["refunds_due"]], [credit_note.name]
		)

		with (
			patch(
				"frappe.db.get_value",
				side_effect=lambda *a, **k: "PR-TEST"
				if a and a[0] == "Payment Request"
				else real_get_value(*a, **k),
			),
			patch("frappe.get_doc", side_effect=get_doc),
			patch("frappe.sendmail") as sendmail,
		):
			orders_api.refund(order, credit_note.name)
		self.assertEqual(
			gateway.refund.call_args.args[:3],
			("Payment Request", "PR-TEST", abs(flt(credit_note.grand_total))),
		)
		refund = frappe.db.get_value("Shop Return Request", created["name"], "refund")
		self.assertEqual(frappe.db.get_value("Payment Entry", refund, "reference_no"), "re_test_refund")
		self.assertEqual(frappe.db.get_value("Sales Invoice", credit_note.name, "outstanding_amount"), 0)
		subjects = [call.kwargs["subject"] for call in sendmail.call_args_list]
		self.assertIn(f"Your refund for order {order}", subjects)


real_get_value = frappe.db.get_value
