import frappe
from frappe import _
from frappe.model.document import Document

from shop.routes import STOREFRONT_SEGMENTS, get_prefix, normalize_prefix


class ShopSettings(Document):
	def validate(self):
		self.route_prefix = normalize_prefix(self.route_prefix)
		first_segment = self.route_prefix.split("/")[0] if self.route_prefix else ""
		if first_segment in STOREFRONT_SEGMENTS:
			frappe.throw(
				_("Route Prefix can't start with {0}, the storefront already uses that path.").format(
					frappe.bold(first_segment)
				)
			)

	def on_update(self):
		if not self.active_theme:
			return
		if get_prefix() != normalize_prefix(self.applied_route_prefix):
			from shop.themes import apply_route_prefix

			apply_route_prefix()
