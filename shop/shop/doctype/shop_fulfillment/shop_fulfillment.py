import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime

STAMPS = {
	"Shipped": "shipped_on",
	"Delivered": "delivered_on",
	"Cancelled": "cancelled_on",
}


class ShopFulfillment(Document):
	def before_insert(self):
		self.requested_on = now_datetime()

	def apply_status(self, status: str) -> None:
		"""Record a provider status and stamp the moment it first happened. A parcel that has shipped
		doesn't go back to being prepared when a late or repeated update arrives."""
		if status == self.status or self.status == "Cancelled":
			return
		if self.status in ("Shipped", "Delivered") and status in ("Pending", "Accepted"):
			return
		if self.status == "Delivered" and status == "Shipped":
			return
		self.status = status
		field = STAMPS.get(status)
		if field and not self.get(field):
			self.set(field, now_datetime())
