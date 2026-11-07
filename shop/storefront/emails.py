"""The store's emails are Frappe Email Templates, so they can be edited in the desk.

Shop makes each template once, with the wording below, and never overwrites it. The template is
rendered with `doc` (the order, quotation or lead), `store_name`, and the pieces each email needs,
already formatted (e.g. `lines_html`, `payment_html`, `order_url`). Emails never stop the action
that sends them: a failure is logged.
"""

import frappe
from frappe.model.document import Document

ORDER = "Sales Order"
QUOTATION = "Quotation"
LEAD = "Lead"

# key: (Email Template name, reference doctype, subject, body)
TEMPLATES = {
	"order_confirmed": (
		"Shop: Order Confirmed", ORDER,
		"Your order {{ doc.name }} is confirmed",
		"<p>Thank you for your order at {{ store_name }}.</p>"
		"<p>Order: <b>{{ doc.name }}</b></p>{{ lines_html }}{{ payment_html }}"
		'<p><a href="{{ order_url }}">View your order</a></p>{{ terms_html }}',
	),
	"order_payment_instructions": (
		"Shop: Order Payment Instructions", ORDER,
		"Your order {{ doc.name }}: how to pay",
		"<p>Thank you for your order at {{ store_name }}.</p>"
		"<p>Order: <b>{{ doc.name }}</b></p>{{ lines_html }}{{ payment_html }}"
		'<p><a href="{{ order_url }}">View your order</a></p>{{ terms_html }}',
	),
	"payment_received": (
		"Shop: Payment Received", ORDER,
		"Payment received for order {{ doc.name }}",
		"<p>Thank you, we've received your payment for order {{ doc.name }}. We'll let you know when it ships.</p>"
		"{% if invoice %}<p>Your invoice {{ invoice }} is attached.</p>{% endif %}",
	),
	"order_cancelled": (
		"Shop: Order Cancelled", ORDER,
		"Your order {{ doc.name }} has been cancelled",
		"<p>We didn't receive payment for order {{ doc.name }} by {{ due_date }}, so it has been cancelled.</p>"
		"<p>If you still want the items, please place a new order at {{ store_name }}. If you have paid, reply to "
		"this email and we'll sort it out.</p>",
	),
	"order_shipped": (
		"Shop: Order Shipped", ORDER,
		"Your order {{ doc.name }} is on its way",
		"<p>Good news: your order {{ doc.name }} has shipped.</p>"
		"{% if carrier_line %}<p>{{ carrier_line }}</p>{% endif %}"
		'{% if tracking_url %}<p><a href="{{ tracking_url }}">Track your parcel</a></p>{% endif %}'
		'<p><a href="{{ order_url }}">View your order</a></p>',
	),
	"order_delivered": (
		"Shop: Order Delivered", ORDER,
		"Your order {{ doc.name }} has been delivered",
		"<p>Your order {{ doc.name }} has been delivered. Thank you for shopping with us.</p>"
		'<p><a href="{{ order_url }}">View your order</a></p>',
	),
	"quote_request_received": (
		"Shop: Quote Request Received", QUOTATION,
		"We've received your quote request {{ doc.name }}",
		"<p>Thanks, we've received your request. We'll check it and email you the quote, usually within one "
		"working day.</p>",
	),
	"quote_request_staff": (
		"Shop: New Quote Request", QUOTATION,
		"Quote request {{ doc.name }} from {{ customer_name | e }}",
		"<p>{{ customer_name | e }} ({{ email | e }}) asked for a quote.</p><p>{{ message | e }}</p>"
		'<p><a href="{{ desk_url }}">Open the quotation</a></p>',
	),
	"quote_ready": (
		"Shop: Quote Ready", QUOTATION,
		"Your quote {{ doc.name }}",
		"<p>Here is your quote. It's valid until {{ valid_till }}.</p>"
		'<p><a href="{{ quote_url }}">View and accept the quote</a></p>',
	),
	"trade_application_received": (
		"Shop: Trade Application Received", LEAD,
		"We've received your trade account application",
		"<p>Thanks for applying for a {{ store_name }} trade account for {{ doc.company_name | e }}. We'll review it "
		"and email you.</p>",
	),
	"trade_application_staff": (
		"Shop: New Trade Application", LEAD,
		"Trade account application: {{ doc.company_name | e }}",
		"<p>{{ doc.company_name | e }} ({{ (doc.country or '') | e }}) applied for a trade account.</p>"
		'<p><a href="{{ desk_url }}">Review the application</a></p>',
	),
	"trade_account_approved": (
		"Shop: Trade Account Approved", LEAD,
		"Your {{ store_name }} trade account is ready",
		"<p>Your trade account for {{ doc.company_name | e }} is approved. Sign in to see your trade prices"
		"{% if on_account %} and pay on account{% endif %}.</p>"
		'<p><a href="{{ sign_in_url }}">Sign in</a></p>',
	),
	"trade_application_declined": (
		"Shop: Trade Application Declined", LEAD,
		"Your {{ store_name }} trade account application",
		"<p>Thank you for applying. We can't open a trade account for {{ doc.company_name | e }} at the moment.</p>"
		"{% if reason %}<p>{{ reason | e }}</p>{% endif %}<p>You can still buy at retail prices in the store.</p>",
	),
}


def ensure_templates() -> None:
	"""Make any missing store Email Template with its default wording; existing ones are left as edited."""
	if not frappe.db.table_exists("Email Template"):
		return
	for name, reference_doctype, subject, body in TEMPLATES.values():
		if frappe.db.exists("Email Template", name):
			continue
		doc = frappe.new_doc("Email Template")
		doc.update(
			{
				"__newname": name,
				"subject": subject,
				"use_html": 1,
				"response_html": body,
				"reference_doctype": reference_doctype if frappe.db.exists("DocType", reference_doctype) else None,
			}
		)
		doc.insert(ignore_permissions=True)


def render(key: str, doc, **context) -> tuple[str, str]:
	"""The subject and body of a store email for this document."""
	name = TEMPLATES[key][0]
	if not frappe.db.exists("Email Template", name):
		ensure_templates()
	template = frappe.get_doc("Email Template", name)
	settings = frappe.get_cached_doc("Shop Settings")
	args = {
		"doc": doc.as_dict() if isinstance(doc, Document) else doc,
		"store_name": settings.store_name or frappe.get_cached_value("Company", settings.company, "company_name"),
		**context,
	}
	return template.get_formatted_subject(args), template.get_formatted_response(args)


def send(key: str, recipients, doc, attachments: list | None = None, **context) -> None:
	"""Send a store email to `recipients`, linked to `doc`; a failure is logged, never raised."""
	recipients = [r for r in (recipients if isinstance(recipients, list) else [recipients]) if r]
	if not recipients:
		return
	try:
		subject, message = render(key, doc, **context)
		frappe.sendmail(
			recipients=recipients,
			subject=subject,
			message=message,
			attachments=attachments or [],
			reference_doctype=doc.doctype,
			reference_name=doc.name,
		)
	except Exception:
		frappe.log_error(title=f"Store email failed: {key}")
