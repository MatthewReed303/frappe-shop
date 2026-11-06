from frappe.model.document import Document


class ShopTradeApplication(Document):
	"""A business asking for a trade account. Approving it (shop.storefront.accounts.approve_trade)
	makes or updates the Customer with its trade terms and gives the applicant a login."""
