import frappe
from frappe import _
from frappe.utils import cint


def get_stock(item_codes: list[str]) -> dict[str, float]:
	if not item_codes:
		return {}
	settings = frappe.get_cached_doc("Shop Settings")
	rows = frappe.get_all(
		"Bin",
		filters={"item_code": ["in", item_codes], "warehouse": settings.default_warehouse},
		fields=["item_code", "actual_qty"],
	)
	qty_map = dict.fromkeys(item_codes, 0.0)
	qty_map.update({row.item_code: row.actual_qty for row in rows})
	return qty_map


def is_stock_item(item_code: str) -> bool:
	return bool(frappe.get_cached_value("Item", item_code, "is_stock_item"))


def available_qty(item_code: str) -> float | None:
	"""How many the store can sell: None when it isn't limited (a service, or the store sells
	out-of-stock items)."""
	if frappe.get_cached_doc("Shop Settings").allow_out_of_stock or not is_stock_item(item_code):
		return None
	return get_stock([item_code]).get(item_code, 0)


def availability(item_code: str, priced: bool = True) -> dict:
	"""Whether the item can be bought online, and what the product page says about it."""
	if not priced:
		return state(False, "unavailable", _("Not available online"))
	qty = available_qty(item_code)
	if qty is None:
		return state(True, "in", _("In stock") if is_stock_item(item_code) else _("Available"))
	if qty <= 0:
		return state(False, "out", _("Out of stock"))
	threshold = cint(frappe.get_cached_doc("Shop Settings").get("low_stock_threshold"))
	if threshold and qty <= threshold:
		return state(True, "low", _("Only {0} left").format(cint(qty)))
	return state(True, "in", _("In stock"))


def state(purchasable: bool, stock_state: str, stock_status: str) -> dict:
	return {"purchasable": purchasable, "stock_state": stock_state, "stock_status": stock_status}


def is_in_stock(item_code: str) -> bool:
	settings = frappe.get_cached_doc("Shop Settings")
	if settings.allow_out_of_stock:
		return True
	if not frappe.get_cached_value("Item", item_code, "is_stock_item"):
		return True
	return get_stock([item_code]).get(item_code, 0) > 0
