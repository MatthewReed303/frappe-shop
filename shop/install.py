import os

import click

import frappe


def after_install():
	setup()


def after_migrate():
	setup()


def setup():
	create_shop_manager_role()
	create_order_fields()
	sync_templates()
	apply_default_theme()
	apply_route_prefix()
	sync_agent()
	enable_customer_signup()
	warn_if_server_scripts_disabled()


def enable_customer_signup():
	"""Shoppers need accounts to track orders, so the store cannot ship with signup off.

	When the store requires its terms, accounts are made on the store's own sign-up page,
	which asks for them, so the site's generic sign-up stays off."""
	if frappe.db.get_single_value("Shop Settings", "require_terms"):
		frappe.db.set_single_value("Website Settings", "disable_signup", 1)
	elif frappe.db.get_single_value("Website Settings", "disable_signup"):
		frappe.db.set_single_value("Website Settings", "disable_signup", 0)


def sync_agent():
	from shop.agent.setup import sync

	sync()


def order_fields() -> dict:
	"""What the store records on each Sales Order: how it is paid and what was agreed at checkout."""
	return {
		"Sales Order": [
			{"fieldname": "shop_section", "fieldtype": "Section Break", "label": "Online Store",
			 "insert_after": "terms", "collapsible": 1},
			{"fieldname": "shop_payment_method", "fieldtype": "Data", "label": "Payment Method",
			 "read_only": 1, "insert_after": "shop_section", "allow_on_submit": 1},
			{"fieldname": "shop_payment_due", "fieldtype": "Date", "label": "Payment Due",
			 "insert_after": "shop_payment_method", "allow_on_submit": 1,
			 "description": "Unpaid store orders are cancelled after this date"},
			{"fieldname": "shop_payment_reference", "fieldtype": "Data", "label": "Payment Reference",
			 "read_only": 1, "insert_after": "shop_payment_due", "allow_on_submit": 1},
			{"fieldname": "shop_shipping_option", "fieldtype": "Small Text", "label": "Shipping Chosen",
			 "read_only": 1, "insert_after": "shop_payment_reference"},
			{"fieldname": "shop_column", "fieldtype": "Column Break", "insert_after": "shop_shipping_option"},
			{"fieldname": "shop_terms_version", "fieldtype": "Data", "label": "Terms Accepted",
			 "read_only": 1, "insert_after": "shop_column"},
			{"fieldname": "shop_terms_accepted_on", "fieldtype": "Datetime", "label": "Terms Accepted On",
			 "read_only": 1, "insert_after": "shop_terms_version"},
			{"fieldname": "shop_business_buyer", "fieldtype": "Check", "label": "Business Buyer",
			 "read_only": 1, "insert_after": "shop_terms_accepted_on",
			 "description": "Confirmed at checkout that they are buying for business purposes"},
			{"fieldname": "shop_access_token", "fieldtype": "Data", "label": "Access Token", "hidden": 1,
			 "read_only": 1, "no_copy": 1, "insert_after": "shop_business_buyer"},
		],
		"Quotation": [
			{"fieldname": "shop_section", "fieldtype": "Section Break", "label": "Online Store",
			 "insert_after": "terms", "collapsible": 1},
			{"fieldname": "shop_request_message", "fieldtype": "Small Text", "label": "Customer's Request",
			 "read_only": 1, "insert_after": "shop_section"},
			{"fieldname": "shop_shipping_option", "fieldtype": "Small Text", "label": "Shipping Chosen",
			 "read_only": 1, "insert_after": "shop_request_message"},
			{"fieldname": "shop_access_token", "fieldtype": "Data", "label": "Access Token", "hidden": 1,
			 "read_only": 1, "no_copy": 1, "insert_after": "shop_shipping_option"},
		],
		"Customer": [
			{"fieldname": "shop_allow_on_account", "fieldtype": "Check", "label": "Pay on Account at the Store",
			 "insert_after": "payment_terms",
			 "description": "Can order online and pay by invoice under their payment terms and credit limit"},
		],
		"User": [
			{"fieldname": "shop_terms_version", "fieldtype": "Data", "label": "Store Terms Accepted",
			 "read_only": 1, "insert_after": "user_type"},
			{"fieldname": "shop_terms_accepted_on", "fieldtype": "Datetime", "label": "Store Terms Accepted On",
			 "read_only": 1, "insert_after": "shop_terms_version"},
		],
	}


def create_order_fields():
	from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

	create_custom_fields(order_fields(), ignore_validate=True)


def create_shop_manager_role():
	if frappe.db.exists("Role", "Shop Manager"):
		return
	frappe.get_doc({"doctype": "Role", "role_name": "Shop Manager", "desk_access": 1}).insert(
		ignore_permissions=True
	)


def sync_templates():
	if not os.path.exists(frappe.get_app_path("shop", "builder_templates")):
		return
	from builder.template_sync import sync_builder_templates

	from shop.themes import organize_template_folders

	sync_builder_templates(app="shop", publish=False)
	organize_template_folders()


def apply_default_theme():
	from shop.themes import ensure_default_theme

	ensure_default_theme()


def apply_route_prefix():
	"""Keep the live storefront on the configured route prefix.

	Migrate re-imports theme components with root links, so this runs every time.
	"""
	if not frappe.db.get_single_value("Shop Settings", "active_theme"):
		return
	from shop.themes import apply_route_prefix as apply

	apply()


def warn_if_server_scripts_disabled():
	from frappe.utils.safe_exec import is_safe_exec_enabled

	if not is_safe_exec_enabled():
		click.secho(
			"Shop storefront pages require server scripts. "
			"Run: bench set-config --global server_script_enabled 1",
			fg="yellow",
		)


def before_tests():
	frappe.clear_cache()
	from shop.demo import setup as setup_demo_data

	setup_demo_data()
