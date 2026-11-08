"""Trade (B2B) basics for the Shop admin, kept as ERPNext records: the store's trade Customer Group,
customers' trade terms, and Pricing Rules giving the group a discount on Item Groups. Anything
finer, such as customer prices or quantity breaks, is set up in ERPNext."""

import frappe
from frappe import _
from frappe.utils import flt

from shop.api import only_managers

APPLICATION_SOURCE = "Store Trade Application"


def trade_group() -> str | None:
	return frappe.get_cached_doc("Shop Settings").get("trade_customer_group")


@frappe.whitelist()
def get_trade() -> dict:
	only_managers()
	group = trade_group()
	return {
		"customer_group": group,
		"payment_terms": frappe.db.get_value("Customer Group", group, "payment_terms") if group else None,
		"discounts": discounts(group) if group else [],
		"item_groups": frappe.get_all("Item Group", order_by="lft", pluck="name"),
		"payment_terms_templates": frappe.get_all("Payment Terms Template", pluck="name"),
	}


def discounts(group: str) -> list[dict]:
	"""The group's Pricing Rules taking a percentage off an Item Group."""
	rules = frappe.get_all(
		"Pricing Rule",
		filters={
			"applicable_for": "Customer Group",
			"customer_group": group,
			"apply_on": "Item Group",
			"rate_or_discount": "Discount Percentage",
			"selling": 1,
			"disable": 0,
		},
		fields=["name", "discount_percentage"],
		order_by="creation",
	)
	for rule in rules:
		rule["item_group"] = frappe.db.get_value(
			"Pricing Rule Item Group", {"parent": rule.name, "parenttype": "Pricing Rule"}, "item_group"
		)
	return rules


@frappe.whitelist(methods=["POST"])
def set_discount(item_group: str, percent: float) -> list[dict]:
	"""Percent off an Item Group for the trade group: its Pricing Rule, made or updated."""
	only_managers()
	group = trade_group()
	if not group:
		frappe.throw(_("Choose the trade customer group first"))
	if not 0 < flt(percent) <= 100:
		frappe.throw(_("The discount is a percentage between 0 and 100"))
	existing = next((rule for rule in discounts(group) if rule.item_group == item_group), None)
	if existing:
		rule = frappe.get_doc("Pricing Rule", existing.name)
		rule.discount_percentage = flt(percent)
		rule.save(ignore_permissions=True)
	else:
		from shop.setup_store import group_discount

		group_discount(group, item_group, flt(percent))
	prioritise(group)
	return discounts(group)


def prioritise(group: str) -> None:
	"""A discount on an Item Group inside another (Products in All Item Groups) wins over the outer
	one: ERPNext picks the Pricing Rule with the higher priority instead of refusing both."""
	for rule in discounts(group):
		lft, rgt = frappe.db.get_value("Item Group", rule.item_group, ["lft", "rgt"]) or (0, 0)
		depth = frappe.db.count("Item Group", {"lft": ["<", lft], "rgt": [">", rgt]})
		priority = str(min(depth + 1, 20))
		if frappe.db.get_value("Pricing Rule", rule.name, "priority") != priority:
			frappe.db.set_value("Pricing Rule", rule.name, {"has_priority": 1, "priority": priority})


@frappe.whitelist(methods=["POST"])
def remove_discount(rule: str) -> list[dict]:
	only_managers()
	group = trade_group()
	if frappe.db.get_value("Pricing Rule", rule, "customer_group") != group:
		frappe.throw(_("This isn't one of the trade discounts"))
	frappe.delete_doc("Pricing Rule", rule, ignore_permissions=True)
	return discounts(group)


@frappe.whitelist(methods=["POST"])
def make_trade_customer(
	customer: str,
	payment_terms: str | None = None,
	allow_on_account: int = 0,
	credit_limit: float | None = None,
) -> dict:
	"""Make a customer a trade customer, with the terms an approved application gets."""
	from shop.storefront.accounts import set_trade_terms

	only_managers()
	if not trade_group():
		frappe.throw(_("Choose the trade customer group in Settings first"))
	if int(allow_on_account or 0) and not payment_terms:
		frappe.throw(_("Set the Payment Terms to let them pay on account"))
	doc = frappe.get_doc("Customer", customer)
	set_trade_terms(doc, None, payment_terms, credit_limit, allow_on_account)
	doc.save(ignore_permissions=True)
	return trade_status(customer)


@frappe.whitelist(methods=["POST"])
def end_trade(customer: str) -> dict:
	"""Back to the group new customers join, without the trade terms approval gave: paying on
	account, payment terms, price list and the store company's credit limit."""
	only_managers()
	group = frappe.db.get_single_value("Selling Settings", "customer_group")
	if not group:
		frappe.throw(_("Set the default Customer Group in Selling Settings first, for the customer to move to"))
	doc = frappe.get_doc("Customer", customer)
	doc.customer_group = group
	doc.payment_terms = None
	doc.default_price_list = None
	company = frappe.get_cached_doc("Shop Settings").company
	doc.set("credit_limits", [row for row in doc.get("credit_limits") or [] if row.company != company])
	if doc.meta.has_field("shop_allow_on_account"):
		doc.shop_allow_on_account = 0
	doc.save(ignore_permissions=True)
	return trade_status(customer)


def trade_status(customer: str) -> dict:
	doc = frappe.get_doc("Customer", customer)
	company = frappe.get_cached_doc("Shop Settings").company
	limit = next((row.credit_limit for row in doc.get("credit_limits") or [] if row.company == company), None)
	group = trade_group()
	return {
		"enabled": bool(group),
		"is_trade": bool(group) and doc.customer_group == group,
		"customer_group": doc.customer_group,
		"payment_terms": doc.payment_terms,
		"allow_on_account": doc.get("shop_allow_on_account") or 0,
		"credit_limit": limit,
	}


def waiting_applications() -> int | None:
	"""Trade applications from the store not answered yet; None when the store has no trade accounts."""
	if not trade_group():
		return None
	return frappe.db.count(
		"Lead",
		{
			"utm_source": APPLICATION_SOURCE,
			"status": ["!=", "Converted"],
			"qualification_status": ["!=", "Unqualified"],
		},
	)
