"""Customer accounts: sign-up with the store's terms, trade account applications and approval,
and the account overview (orders, invoices, quotes)."""

import frappe
from frappe import _
from frappe.rate_limiter import rate_limit
from frappe.utils import cint, flt, now_datetime, nowdate, validate_email_address

from shop.routes import store_url
from shop.storefront import pricing

APPROVER_ROLES = ("System Manager", "Sales Manager", "Shop Manager")


def terms_required() -> bool:
	return bool(frappe.get_cached_doc("Shop Settings").get("require_terms"))


def terms_version() -> str | None:
	from shop.storefront import terms

	return terms.version()


def check_terms(accept_terms) -> None:
	if terms_required() and not cint(accept_terms):
		frappe.throw(_("Please accept the terms and conditions"))


# --- sign-up ---------------------------------------------------------------------


@frappe.whitelist(allow_guest=True)
def signup_url() -> str | None:
	"""The store's sign-up page, when it replaces the site's own sign-up."""
	if not frappe.db.get_single_value("Website Settings", "disable_signup"):
		return None
	return store_url("account/register")


@frappe.whitelist(allow_guest=True, methods=["POST"])
@rate_limit(limit=5, seconds=60 * 10)
def register(full_name: str, email: str, accept_terms: int = 0) -> dict:
	"""Create a store account. The customer accepts the terms here; a link to set the password
	is emailed."""
	check_terms(accept_terms)
	email = (email or "").strip().lower()
	full_name = (full_name or "").strip()
	validate_email_address(email, throw=True)
	if not full_name:
		frappe.throw(_("Please enter your name"))
	if frappe.db.exists("User", email):
		frappe.throw(_("There's already an account for {0}. Sign in, or reset your password.").format(email))
	from shop.storefront.checkout import elevated

	with elevated():
		create_user(email, full_name)
		ensure_customer(email, full_name)
	return {"message": _("Check your email for a link to set your password.")}


def create_user(email: str, full_name: str, send_welcome: bool = True) -> str:
	first, _space, last = full_name.partition(" ")
	user = frappe.get_doc(
		{
			"doctype": "User",
			"email": email,
			"first_name": first,
			"last_name": last.strip() or None,
			"user_type": "Website User",
			"send_welcome_email": 1 if send_welcome else 0,
		}
	)
	if frappe.get_meta("User").has_field("shop_terms_version") and terms_required():
		user.shop_terms_version = terms_version()
		user.shop_terms_accepted_on = now_datetime()
	user.flags.ignore_permissions = True
	user.insert(ignore_permissions=True)
	if frappe.db.exists("Role", "Customer"):
		user.add_roles("Customer")
	return user.name


def ensure_customer(email: str, full_name: str) -> str:
	from shop.storefront.checkout import create_contact, find_customer_by_email

	existing = find_customer_by_email(email)
	if existing:
		return existing
	party = frappe.get_doc({"doctype": "Customer", "customer_name": full_name, "customer_type": "Individual"}).insert(
		ignore_permissions=True
	)
	create_contact(party.name, {"full_name": full_name}, email)
	return party.name


# --- trade accounts ------------------------------------------------------------------

TRADE_FIELDS = (
	"company_name", "company_number", "tax_id", "industry", "website", "country", "message",
	"contact_name", "email", "phone", "address_line1", "address_line2", "city", "state", "pincode",
)


def trade_source() -> str:
	from shop.install import TRADE_SOURCE

	return TRADE_SOURCE


@frappe.whitelist(allow_guest=True, methods=["POST"])
@rate_limit(limit=5, seconds=60 * 10)
def apply_trade(application: dict, accept_terms: int = 0) -> dict:
	"""A business applies for a trade account: an ERPNext Lead (UTM Source "Store Trade Application")
	with its contact, address and the terms they accepted. Staff approve it from the Lead."""
	check_terms(accept_terms)
	values = {field: (application.get(field) or "").strip() for field in TRADE_FIELDS}
	values["email"] = values["email"].lower()
	validate_email_address(values["email"], throw=True)
	for field, label in (("company_name", _("business name")), ("contact_name", _("your name")), ("country", _("country"))):
		if not values[field]:
			frappe.throw(_("Please enter {0}").format(label))
	from shop.storefront.checkout import elevated
	from shop.storefront.shipping import valid_country

	values["country"] = valid_country(values["country"])
	if values["industry"] and not frappe.db.exists("Industry Type", values["industry"]):
		values["industry"] = ""
	with elevated():
		if pending_application(values["email"]):
			frappe.throw(_("We already have an application from {0}. We'll be in touch soon.").format(values["email"]))
		lead = frappe.get_doc(
			{
				"doctype": "Lead",
				"lead_name": values["contact_name"],
				"company_name": values["company_name"],
				"email_id": values["email"],
				"phone": values["phone"],
				"website": values["website"],
				"country": values["country"],
				"city": values["city"],
				"state": values["state"],
				"industry": values["industry"] or None,
				"company": frappe.get_cached_doc("Shop Settings").company,
				"utm_source": trade_source(),
				"status": "Open",
				"qualification_status": "In Process",
				"shop_tax_id": values["tax_id"],
				"shop_company_number": values["company_number"],
			}
		)
		if values["message"]:
			lead.append("notes", {"note": frappe.utils.escape_html(values["message"]), "added_on": now_datetime()})
		if terms_required():
			lead.shop_terms_version = terms_version()
			lead.shop_terms_accepted_on = now_datetime()
		user = pricing.shopper()
		if user not in ("Guest", None) and user == values["email"]:
			lead.shop_user = user
		lead.insert(ignore_permissions=True)
		if values["address_line1"] and values["city"]:
			add_address("Lead", lead.name, values)
		notify_staff_of_application(lead)
		store = frappe.get_cached_doc("Shop Settings").store_name or _("our store")
		send(values["email"], _("We've received your trade account application"),
			 _("<p>Thanks for applying for a {0} trade account for {1}. We'll review it and email you.</p>").format(
				 frappe.utils.escape_html(store), frappe.utils.escape_html(values["company_name"])),
			 "Lead", lead.name)
	return {"name": lead.name, "message": _("Thanks. We'll review your application and email you.")}


def pending_application(email: str) -> str | None:
	"""An open trade application (Lead) from this email, not yet approved or turned down."""
	return frappe.db.get_value(
		"Lead",
		{"email_id": email, "utm_source": trade_source(), "status": ["!=", "Converted"],
		 "qualification_status": ["!=", "Unqualified"]},
	)


def notify_staff_of_application(lead) -> None:
	recipients = staff_emails()
	if not recipients:
		return
	link = frappe.utils.get_url(f"/desk/lead/{lead.name}")
	send(recipients, _("Trade account application: {0}").format(lead.company_name),
		 f"<p>{frappe.utils.escape_html(lead.company_name)} ({frappe.utils.escape_html(lead.country or '')}) applied for a trade account.</p>"
		 f'<p><a href="{link}">Review the application</a></p>',
		 "Lead", lead.name)


def staff_emails() -> list[str]:
	users = frappe.get_all(
		"Has Role", filters={"role": "Shop Manager", "parenttype": "User"}, pluck="parent", distinct=True
	)
	return [
		u for u in frappe.get_all("User", filters={"name": ["in", users or [""]], "enabled": 1}, pluck="name")
		if u not in ("Administrator", "Guest")
	]


@frappe.whitelist(methods=["POST"])
def approve_trade(
	lead: str,
	customer_group: str | None = None,
	payment_terms: str | None = None,
	credit_limit: float | None = None,
	allow_on_account: int = 0,
	price_list: str | None = None,
) -> dict:
	"""Approve a trade application: ERPNext makes the Customer from the Lead (moving its contact and
	address over and marking it Converted), or an existing customer with that email is converted.
	The trade terms are set on the Customer and the applicant gets a store login."""
	frappe.only_for(APPROVER_ROLES)
	doc = frappe.get_doc("Lead", lead)
	if doc.utm_source != trade_source():
		frappe.throw(_("This Lead is not a trade account application"))
	if doc.status == "Converted":
		frappe.throw(_("This application has already been approved"))
	if cint(allow_on_account) and not payment_terms:
		frappe.throw(_("Set the Payment Terms to let them pay on account"))
	from erpnext.crm.doctype.lead.lead import _make_customer

	from shop.storefront.checkout import find_customer_by_email

	existing = find_customer_by_email(doc.email_id)
	if existing:
		customer = frappe.get_doc("Customer", existing)
		customer.customer_name = doc.company_name
		customer.customer_type = "Company"
		customer.lead_name = doc.name
	else:
		customer = _make_customer(doc.name, ignore_permissions=True)
	customer.customer_group = customer_group or frappe.get_cached_doc("Shop Settings").get("trade_customer_group") or customer.customer_group
	customer.default_price_list = price_list or None
	customer.tax_id = doc.shop_tax_id or customer.tax_id
	customer.payment_terms = payment_terms or None
	if customer.meta.has_field("shop_allow_on_account"):
		customer.shop_allow_on_account = 1 if cint(allow_on_account) else 0
	company = frappe.get_cached_doc("Shop Settings").company
	customer.credit_limits = [row for row in customer.get("credit_limits") or [] if row.company != company]
	if flt(credit_limit):
		customer.append("credit_limits", {"company": company, "credit_limit": flt(credit_limit)})
	user = doc.shop_user or (doc.email_id if frappe.db.exists("User", doc.email_id) else None) or create_user(doc.email_id, doc.lead_name)
	if user not in [row.user for row in customer.get("portal_users") or []]:
		customer.append("portal_users", {"user": user})
	customer.flags.ignore_permissions = True
	customer.save(ignore_permissions=True) if existing else customer.insert(ignore_permissions=True)
	if existing:
		# a customer made before applying keeps its own contact; the Lead's address joins it
		for address in frappe.get_all("Dynamic Link", filters={"parenttype": "Address", "link_doctype": "Lead", "link_name": doc.name}, pluck="parent"):
			address_doc = frappe.get_doc("Address", address)
			address_doc.append("links", {"link_doctype": "Customer", "link_name": customer.name})
			address_doc.save(ignore_permissions=True)
	doc.reload()
	doc.db_set({"status": "Converted", "customer": customer.name, "qualification_status": "Qualified",
				"qualified_by": frappe.session.user, "qualified_on": nowdate()})
	store = frappe.get_cached_doc("Shop Settings").store_name or _("store")
	send(doc.email_id, _("Your {0} trade account is ready").format(store),
		 _("<p>Your trade account for {0} is approved. Sign in to see your trade prices{1}.</p>"
		   "<p><a href=\"{2}\">Sign in</a></p>").format(
			 frappe.utils.escape_html(doc.company_name),
			 _(" and pay on account") if cint(allow_on_account) else "",
			 frappe.utils.get_url("/login?redirect-to=" + store_url("account/orders")),
		 ),
		 "Lead", doc.name)
	return {"customer": customer.name, "user": user}


def add_address(link_doctype: str, link_name: str, values: dict) -> None:
	frappe.get_doc(
		{
			"doctype": "Address",
			"address_title": values.get("company_name") or values.get("contact_name"),
			"address_type": "Billing",
			"address_line1": values.get("address_line1"),
			"address_line2": values.get("address_line2"),
			"city": values.get("city"),
			"state": values.get("state"),
			"pincode": values.get("pincode"),
			"country": values.get("country"),
			"phone": values.get("phone"),
			"email_id": values.get("email"),
			"links": [{"link_doctype": link_doctype, "link_name": link_name}],
		}
	).insert(ignore_permissions=True)


@frappe.whitelist(methods=["POST"])
def decline_trade(lead: str, reason: str | None = None) -> None:
	"""Turn down a trade application: the Lead is marked Unqualified, with the reason as a note."""
	frappe.only_for(APPROVER_ROLES)
	doc = frappe.get_doc("Lead", lead)
	if doc.utm_source != trade_source() or doc.status == "Converted" or doc.qualification_status == "Unqualified":
		frappe.throw(_("This application has already been dealt with"))
	if reason:
		doc.append("notes", {"note": frappe.utils.escape_html(reason), "added_by": frappe.session.user, "added_on": now_datetime()})
	doc.qualification_status = "Unqualified"
	doc.save(ignore_permissions=True)
	store = frappe.get_cached_doc("Shop Settings").store_name or _("store")
	send(doc.email_id, _("Your {0} trade account application").format(store),
		 _("<p>Thank you for applying. We can't open a trade account for {0} at the moment.</p>{1}"
		   "<p>You can still buy at retail prices in the store.</p>").format(
			 frappe.utils.escape_html(doc.company_name),
			 f"<p>{frappe.utils.escape_html(reason)}</p>" if reason else "",
		 ),
		 "Lead", doc.name)


def send(recipients, subject: str, message: str, doctype: str, name: str) -> None:
	from shop.storefront.payment import send_email

	send_email(
		recipients=recipients if isinstance(recipients, list) else [recipients],
		subject=subject,
		message=message,
		reference_doctype=doctype,
		reference_name=name,
	)


# --- account overview --------------------------------------------------------------


def trade_status() -> dict | None:
	"""The signed-in shopper's trade account: approved terms, or where their application stands."""
	customer = pricing.session_customer()
	user = pricing.shopper()
	if user in ("Guest", None):
		return None
	application = frappe.db.get_value(
		"Lead", {"email_id": user, "utm_source": trade_source()}, ["name", "status", "qualification_status"],
		as_dict=True, order_by="creation desc",
	)
	state = None
	if application:
		state = "Approved" if application.status == "Converted" else (
			"Declined" if application.qualification_status == "Unqualified" else "Pending")
	if not customer:
		return {"status": state} if state else None
	fields = ["customer_group", "payment_terms", "lead_name"] + (
		["shop_allow_on_account"] if frappe.get_meta("Customer").has_field("shop_allow_on_account") else [])
	values = frappe.db.get_value("Customer", customer, fields, as_dict=True)
	settings = frappe.get_cached_doc("Shop Settings")
	trade = (
		pricing.customer_price_list(customer) != settings.price_list
		or (settings.get("trade_customer_group") and values.customer_group == settings.trade_customer_group)
		or state == "Approved"
	)
	return {
		"trade": bool(trade),
		"status": state,
		"on_account": bool(values.get("shop_allow_on_account") and values.payment_terms),
		"payment_terms": values.payment_terms,
	}


def invoices(customers: list[str]) -> list[dict]:
	if not customers:
		return []
	rows = frappe.get_all(
		"Sales Invoice",
		filters={"customer": ["in", customers], "docstatus": 1},
		fields=["name", "posting_date", "due_date", "grand_total", "outstanding_amount", "currency", "status"],
		order_by="posting_date desc",
		limit=50,
	)
	for row in rows:
		row.formatted_total = frappe.utils.fmt_money(row.grand_total, currency=row.currency)
		row.formatted_outstanding = frappe.utils.fmt_money(row.outstanding_amount, currency=row.currency)
		row.pdf_url = f"/api/method/shop.storefront.accounts.invoice_pdf?name={row.name}"
	return rows


@frappe.whitelist(allow_guest=True)
def invoice_pdf(name: str, order: str | None = None, token: str | None = None):
	"""A customer's invoice as a PDF: their own when signed in, or with the order's private link."""
	from shop.storefront.orders import session_customers, valid_token

	customer = frappe.db.get_value("Sales Invoice", {"name": name, "docstatus": 1}, "customer")
	if not customer:
		raise frappe.PermissionError
	from_order = bool(
		order
		and token
		and valid_token(order, token)
		and frappe.db.exists("Sales Invoice Item", {"parent": name, "sales_order": order})
	)
	if not from_order and customer not in session_customers():
		raise frappe.PermissionError
	from shop.storefront.checkout import elevated

	with elevated():
		# name the print format, as the desk does: without it Frappe renders the default format
		# with the site's PDF generator instead of the one the format is built for
		print_format = frappe.get_meta("Sales Invoice").default_print_format or None
		pdf = frappe.get_print("Sales Invoice", name, print_format=print_format, as_pdf=True)
	frappe.local.response.filename = f"{name}.pdf"
	frappe.local.response.filecontent = pdf
	frappe.local.response.type = "pdf"
