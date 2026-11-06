"""Customer accounts: sign-up with the store's terms, trade account applications and approval,
and the account overview (orders, invoices, quotes)."""

import frappe
from frappe import _
from frappe.rate_limiter import rate_limit
from frappe.utils import cint, flt, now_datetime, validate_email_address

from shop.routes import store_url
from shop.storefront import pricing

APPROVER_ROLES = ("System Manager", "Sales Manager", "Shop Manager")


def terms_required() -> bool:
	return bool(frappe.get_cached_doc("Shop Settings").get("require_terms"))


def terms_version() -> str | None:
	return frappe.get_cached_doc("Shop Settings").get("terms_version")


def check_terms(accept_terms) -> None:
	if terms_required() and not cint(accept_terms):
		frappe.throw(_("Please accept the terms and conditions"))


# --- sign-up ---------------------------------------------------------------------


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
	"company_name", "company_number", "tax_id", "business_type", "website", "country", "message",
	"contact_name", "email", "phone", "address_line1", "address_line2", "city", "state", "pincode",
)


@frappe.whitelist(allow_guest=True, methods=["POST"])
@rate_limit(limit=5, seconds=60 * 10)
def apply_trade(application: dict, accept_terms: int = 0) -> dict:
	"""A business applies for a trade account. Staff approve it in the desk."""
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
	if values["business_type"] not in ("", "System integrator", "Distributor or reseller", "Manufacturer", "Utility or farm", "Other"):
		values["business_type"] = "Other"
	with elevated():
		if frappe.db.exists("Shop Trade Application", {"email": values["email"], "status": "Pending"}):
			frappe.throw(_("We already have an application from {0}. We'll be in touch soon.").format(values["email"]))
		doc = frappe.get_doc({"doctype": "Shop Trade Application", **values})
		if terms_required():
			doc.terms_version = terms_version()
			doc.terms_accepted_on = now_datetime()
		user = pricing.shopper()
		if user not in ("Guest", None) and user == values["email"]:
			doc.user = user
		doc.insert(ignore_permissions=True)
		notify_staff_of_application(doc)
		send(values["email"], _("We've received your trade account application"),
			 _("<p>Thanks for applying for a ModBee trade account for {0}. We'll review it and email you, "
			   "usually within two working days.</p>").format(frappe.utils.escape_html(values["company_name"])),
			 "Shop Trade Application", doc.name)
	return {"name": doc.name, "message": _("Thanks. We'll review your application and email you.")}


def notify_staff_of_application(doc) -> None:
	recipients = staff_emails()
	if not recipients:
		return
	link = frappe.utils.get_url(f"/desk/shop-trade-application/{doc.name}")
	send(recipients, _("Trade account application: {0}").format(doc.company_name),
		 f"<p>{frappe.utils.escape_html(doc.company_name)} ({frappe.utils.escape_html(doc.country)}) applied for a trade account.</p>"
		 f'<p><a href="{link}">Review the application</a></p>',
		 "Shop Trade Application", doc.name)


def staff_emails() -> list[str]:
	users = frappe.get_all(
		"Has Role", filters={"role": "Shop Manager", "parenttype": "User"}, pluck="parent", distinct=True
	)
	return [
		u for u in frappe.get_all("User", filters={"name": ["in", users or [""]], "enabled": 1}, pluck="name")
		if u not in ("Administrator", "Guest")
	]


@frappe.whitelist(methods=["POST"])
def approve_trade(name: str) -> dict:
	"""Make or update the Customer with the trade terms set on the application, and give the
	applicant a login."""
	frappe.only_for(APPROVER_ROLES)
	doc = frappe.get_doc("Shop Trade Application", name)
	if doc.status != "Pending":
		frappe.throw(_("This application has already been {0}").format(doc.status.lower()))
	if doc.allow_on_account and not doc.payment_terms:
		frappe.throw(_("Set the Payment Terms to let them pay on account"))
	from shop.storefront.checkout import create_contact, find_customer_by_email

	customer_name = find_customer_by_email(doc.email)
	customer = frappe.get_doc("Customer", customer_name) if customer_name else frappe.new_doc("Customer")
	customer.customer_name = doc.company_name
	customer.customer_type = "Company"
	if doc.customer_group:
		customer.customer_group = doc.customer_group
	customer.default_price_list = doc.price_list or None
	customer.tax_id = doc.tax_id or customer.tax_id
	customer.payment_terms = doc.payment_terms or None
	if customer.meta.has_field("shop_allow_on_account"):
		customer.shop_allow_on_account = 1 if doc.allow_on_account else 0
	company = frappe.get_cached_doc("Shop Settings").company
	customer.credit_limits = [row for row in customer.get("credit_limits") or [] if row.company != company]
	if flt(doc.credit_limit):
		customer.append("credit_limits", {"company": company, "credit_limit": flt(doc.credit_limit)})
	customer.flags.ignore_permissions = True
	customer.save(ignore_permissions=True) if customer_name else customer.insert(ignore_permissions=True)
	if not customer_name:
		create_contact(customer.name, {"full_name": doc.contact_name, "phone": doc.phone}, doc.email)
	if doc.address_line1 and doc.city:
		add_address(customer.name, doc)
	user = doc.user or (doc.email if frappe.db.exists("User", doc.email) else None)
	if not user:
		user = create_user(doc.email, doc.contact_name)
	doc.db_set({"status": "Approved", "customer": customer.name, "user": user})
	send(doc.email, _("Your ModBee trade account is ready"),
		 _("<p>Your trade account for {0} is approved. Sign in to see your trade prices{1}.</p>"
		   "<p><a href=\"{2}\">Sign in</a></p>").format(
			 frappe.utils.escape_html(doc.company_name),
			 _(" and pay on account") if doc.allow_on_account else "",
			 frappe.utils.get_url("/login?redirect-to=" + store_url("account/orders")),
		 ),
		 "Shop Trade Application", doc.name)
	return {"customer": customer.name, "user": user}


def add_address(customer: str, doc) -> None:
	from shop.storefront.checkout import create_address

	create_address(
		customer,
		{"full_name": doc.company_name, "phone": doc.phone, "email": doc.email},
		{"address_line1": doc.address_line1, "address_line2": doc.address_line2, "city": doc.city,
		 "state": doc.state, "pincode": doc.pincode, "country": doc.country},
	)


@frappe.whitelist(methods=["POST"])
def decline_trade(name: str, reason: str | None = None) -> None:
	frappe.only_for(APPROVER_ROLES)
	doc = frappe.get_doc("Shop Trade Application", name)
	if doc.status != "Pending":
		frappe.throw(_("This application has already been {0}").format(doc.status.lower()))
	doc.db_set({"status": "Declined", "decline_reason": reason})
	send(doc.email, _("Your ModBee trade account application"),
		 _("<p>Thank you for applying. We can't open a trade account for {0} at the moment.</p>{1}"
		   "<p>You can still buy at retail prices in the store.</p>").format(
			 frappe.utils.escape_html(doc.company_name),
			 f"<p>{frappe.utils.escape_html(reason)}</p>" if reason else "",
		 ),
		 "Shop Trade Application", doc.name)


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
	customer = pricing.session_customer()
	user = pricing.shopper()
	if not customer:
		application = frappe.db.get_value(
			"Shop Trade Application", {"email": user}, ["name", "status"], as_dict=True, order_by="creation desc"
		) if user not in ("Guest", None) else None
		return {"status": application.status} if application else None
	group, price_list, terms, on_account = frappe.get_cached_value(
		"Customer", customer, ["customer_group", "default_price_list", "payment_terms", "shop_allow_on_account"]
	) if frappe.get_meta("Customer").has_field("shop_allow_on_account") else (
		*frappe.get_cached_value("Customer", customer, ["customer_group", "default_price_list", "payment_terms"]), 0
	)
	settings = frappe.get_cached_doc("Shop Settings")
	trade = pricing.customer_price_list(customer) != settings.price_list
	application = frappe.db.get_value(
		"Shop Trade Application", {"customer": customer}, "status", order_by="creation desc"
	)
	return {
		"trade": trade or application == "Approved",
		"status": application,
		"on_account": bool(on_account and terms),
		"payment_terms": terms,
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


@frappe.whitelist()
def invoice_pdf(name: str):
	"""The signed-in customer's own invoice as a PDF."""
	from shop.storefront.orders import session_customers

	if frappe.db.get_value("Sales Invoice", name, "customer") not in session_customers():
		raise frappe.PermissionError
	from shop.storefront.checkout import elevated

	with elevated():
		pdf = frappe.get_print("Sales Invoice", name, as_pdf=True)
	frappe.local.response.filename = f"{name}.pdf"
	frappe.local.response.filecontent = pdf
	frappe.local.response.type = "pdf"
