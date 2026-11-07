import frappe
from erpnext.accounts.doctype.payment_request.payment_request import PaymentRequest

from shop.routes import store_url

AUTHORIZED_STATUSES = ("Authorized", "Verified", "Completed")


class ShopPaymentRequest(PaymentRequest):
	def on_payment_authorized(self, status: str | None = None) -> str | None:
		"""Called by payments gateway controllers after a successful payment."""
		if status not in AUTHORIZED_STATUSES:
			return None
		self.flags.ignore_permissions = True
		# a gateway can report one payment twice at once (the shopper's return and its webhook)
		newly_paid = frappe.db.get_value(self.doctype, self.name, "status", for_update=True) != "Paid"
		if newly_paid:
			self.reload()
			self.flags.ignore_permissions = True
			self.set_as_paid()
		if self.reference_doctype == "Sales Order" and newly_paid:
			from shop.storefront.checkout import send_confirmation

			send_confirmation(self.reference_name)
		if self.reference_doctype == "Sales Order":
			from shop.fulfillment.service import auto_send

			auto_send(self.reference_name)
		return self.confirmation_url()

	def confirmation_url(self) -> str | None:
		if self.reference_doctype != "Sales Order":
			return None
		from shop.storefront.orders import access_token, order_url

		if access_token(self.reference_name):
			return order_url(self.reference_name)
		return store_url("account/orders")
