import frappe
from frappe.tests import IntegrationTestCase

from shop.storefront import cart, checkout

BUYER = {"email": "payer@example.com", "full_name": "Pay Er", "phone": "9999900000"}
ADDRESS = {"address_line1": "9 Gateway Rd", "city": "Bengaluru", "country": "India", "pincode": "560002"}


class TestPayments(IntegrationTestCase):
	def setUp(self):
		frappe.db.delete("Shop Cart")
		if hasattr(frappe.local, "request"):
			del frappe.local.request
		self.settings = frappe.get_doc("Shop Settings")
		self.gateway_account = frappe.db.get_value("Payment Gateway Account", {}, "name")

	def test_gateway_order_creates_payment_request(self):
		if not self.gateway_account:
			self.skipTest("no payment gateway account configured")
		frappe.db.set_single_value("Shop Settings", "payment_gateway_account", self.gateway_account)
		frappe.get_cached_doc("Shop Settings")
		cart.add_item("SHOP-DEMO-003")
		result = checkout.place_order(customer=BUYER, address=ADDRESS, payment_method="gateway")
		self.assertTrue(result.get("payment_url"))
		payment_request = frappe.get_doc(
			"Payment Request", {"reference_name": result["sales_order"], "docstatus": 1}
		)
		self.assertEqual(payment_request.reference_doctype, "Sales Order")

	def test_on_payment_authorized_marks_paid_and_redirects(self):
		if not self.gateway_account:
			self.skipTest("no payment gateway account configured")
		frappe.db.set_single_value("Shop Settings", "payment_gateway_account", self.gateway_account)
		frappe.get_cached_doc("Shop Settings")
		cart.add_item("SHOP-DEMO-004")
		result = checkout.place_order(customer=BUYER, address=ADDRESS, payment_method="gateway")
		payment_request = frappe.get_doc(
			"Payment Request", {"reference_name": result["sales_order"], "docstatus": 1}
		)
		redirect = payment_request.run_method("on_payment_authorized", "Completed")
		self.assertIn(f"/order-confirmation/{result['sales_order']}", redirect)
		self.assertTrue(
			frappe.db.exists(
				"Payment Entry Reference",
				{"reference_name": result["sales_order"], "docstatus": 1},
			)
		)

	def test_an_empty_checkout_leads_back_to_the_order_waiting_for_payment(self):
		from unittest.mock import patch

		if not self.gateway_account:
			self.skipTest("no payment gateway account configured")
		frappe.db.set_single_value("Shop Settings", "payment_gateway_account", self.gateway_account)
		cart.add_item("SHOP-DEMO-003")
		token = cart.find_cart().token
		result = checkout.place_order(customer=BUYER, address=ADDRESS, payment_method="gateway")
		with patch.object(cart, "request_token", return_value=token):
			waiting = checkout.get_checkout_summary()["waiting_order"]
			self.assertEqual(waiting["pay_url"], result["payment_url"])
			self.assertIn(result["sales_order"], waiting["note"])
			payment_request = frappe.get_doc(
				"Payment Request", {"reference_name": result["sales_order"], "docstatus": 1}
			)
			payment_request.run_method("on_payment_authorized", "Completed")
			self.assertIsNone(checkout.get_checkout_summary()["waiting_order"])

	def test_a_request_for_an_order_entered_in_desk_is_left_to_erpnext(self):
		from unittest.mock import patch

		from erpnext.accounts.doctype.payment_request.payment_request import make_payment_request

		if not self.gateway_account:
			self.skipTest("no payment gateway account configured")
		frappe.db.set_single_value("Shop Settings", "payment_gateway_account", self.gateway_account)
		cart.add_item("SHOP-DEMO-004")
		store_order = frappe.get_doc(
			"Sales Order", checkout.place_order(customer=BUYER, address=ADDRESS)["sales_order"]
		)
		desk_order = frappe.copy_doc(store_order)
		for field in ("shop_payment_method", "shop_access_token", "shop_payment_due"):
			if desk_order.meta.has_field(field):
				desk_order.set(field, None)
		desk_order.insert(ignore_permissions=True).submit()
		payment_request = make_payment_request(
			dt="Sales Order", dn=desk_order.name, submit_doc=1, mute_email=1, return_doc=1
		)
		with patch("shop.fulfillment.service.auto_send") as auto_send:
			self.assertIsNone(payment_request.run_method("on_payment_authorized", "Completed"))
		auto_send.assert_not_called()
		self.assertEqual(frappe.db.get_value("Payment Request", payment_request.name, "status"), "Paid")
