"""Card payments with Stripe PaymentIntents and the Payment Element.

The payments app's Stripe page uses Stripe's legacy Charges API, which cannot run 3-D Secure,
so cards whose bank asks for it are declined. This page uses PaymentIntents instead: 3-D Secure,
Apple Pay and Google Pay work, and the payment is confirmed by Stripe before the order is
marked paid.

Flow: /pay/<order>?token=<cart token> shows the order and Stripe's Payment Element. The
browser asks for a PaymentIntent (create_intent), Stripe confirms the payment (with 3-D Secure
when the bank wants it), then complete() checks the PaymentIntent with Stripe and records the
payment against the order's Payment Request. A Stripe webhook (payment_intent.succeeded) does
the same, in case the shopper closes the page before returning.

Keys come from the Stripe Settings behind the store's payment gateway account.
"""

import hashlib
import hmac
import json
import time

import frappe
from frappe import _
from frappe.rate_limiter import rate_limit
from frappe.utils import cint, flt

from shop.routes import store_url

WEBHOOK_TOLERANCE_SECONDS = 300
# currencies Stripe charges in whole units
ZERO_DECIMAL = {"BIF", "CLP", "DJF", "GNF", "JPY", "KMF", "KRW", "MGA", "PYG", "RWF", "UGX", "VND", "VUV", "XAF", "XOF", "XPF"}


def gateway_settings() -> frappe._dict | None:
	"""The Stripe Settings record behind the store's gateway account, or None."""
	account = frappe.get_cached_doc("Shop Settings").payment_gateway_account
	if not account:
		return None
	gateway = frappe.db.get_value("Payment Gateway Account", account, "payment_gateway")
	settings_doctype, controller = frappe.db.get_value(
		"Payment Gateway", gateway, ["gateway_settings", "gateway_controller"]
	) or (None, None)
	if settings_doctype != "Stripe Settings" or not controller:
		return None
	doc = frappe.get_doc("Stripe Settings", controller)
	return frappe._dict(
		name=controller,
		publishable_key=doc.publishable_key,
		secret_key=doc.get_password("secret_key", raise_exception=False),
	)


def is_stripe() -> bool:
	return gateway_settings() is not None


def pay_url(order_name: str, token: str) -> str:
	return frappe.utils.get_url(f"/pay/{order_name}?token={token}")


def minor_units(amount: float, currency: str) -> int:
	return cint(round(flt(amount), 0)) if currency.upper() in ZERO_DECIMAL else cint(round(flt(amount) * 100))


def client():
	import stripe

	settings = gateway_settings()
	if not settings or not settings.secret_key:
		frappe.throw(_("Card payments are not set up"))
	return stripe.StripeClient(settings.secret_key)


def load_order(order_name: str, token: str | None):
	"""The order, when the cart token proves the visitor placed it."""
	if not order_name or not token:
		raise frappe.PermissionError
	from shop.storefront.orders import valid_token

	if not valid_token(order_name, token):
		raise frappe.PermissionError
	order = frappe.get_doc("Sales Order", order_name)
	if order.docstatus != 1:
		frappe.throw(_("This order is cancelled"))
	return order


def payment_request(order) -> object | None:
	name = frappe.db.get_value(
		"Payment Request",
		{"reference_doctype": "Sales Order", "reference_name": order.name, "docstatus": 1},
		"name",
		order_by="creation desc",
	)
	return frappe.get_doc("Payment Request", name) if name else None


def amount_due(order) -> float:
	from shop.storefront.payment import amount_due as due

	return due(order)


def is_paid(order) -> bool:
	request = payment_request(order)
	return amount_due(order) <= 0 or bool(request and request.status == "Paid")


@frappe.whitelist(allow_guest=True, methods=["POST"])
@rate_limit(limit=20, seconds=60)
def create_intent(order: str, token: str) -> dict:
	"""A PaymentIntent for what is still owed on the order (reused while it is unpaid)."""
	sales_order = load_order(order, token)
	if is_paid(sales_order):
		return {"paid": True, "redirect": confirmation(sales_order, token)}
	stripe = client()
	amount = minor_units(amount_due(sales_order), sales_order.currency)
	existing = sales_order.get("shop_payment_reference")
	if existing:
		intent = stripe.payment_intents.retrieve(existing)
		if intent.status == "succeeded":
			record_payment(intent)
			return {"paid": True, "redirect": confirmation(sales_order, token)}
		if intent.status not in ("canceled",) and intent.currency == sales_order.currency.lower():
			if intent.amount != amount:
				intent = stripe.payment_intents.update(existing, params={"amount": amount})
			return {"client_secret": intent.client_secret}
	intent = stripe.payment_intents.create(
		params={
			"amount": amount,
			"currency": sales_order.currency.lower(),
			"automatic_payment_methods": {"enabled": True},
			"description": _("Order {0}").format(sales_order.name),
			"receipt_email": sales_order.contact_email or None,
			"metadata": {"sales_order": sales_order.name, "site": frappe.local.site},
		},
		options={"idempotency_key": f"{frappe.local.site}-{sales_order.name}-{amount}"},
	)
	frappe.db.set_value("Sales Order", sales_order.name, "shop_payment_reference", intent.id, update_modified=False)
	return {"client_secret": intent.client_secret}


@frappe.whitelist(allow_guest=True, methods=["POST"])
@rate_limit(limit=20, seconds=60)
def complete(order: str, token: str, payment_intent: str) -> dict:
	"""After Stripe confirms: check the PaymentIntent with Stripe and record the payment."""
	sales_order = load_order(order, token)
	intent = client().payment_intents.retrieve(payment_intent)
	if (intent.metadata or {}).get("sales_order") != sales_order.name:
		raise frappe.PermissionError
	if intent.status == "succeeded":
		record_payment(intent)
		return {"paid": True, "redirect": confirmation(sales_order, token)}
	if intent.status == "processing":
		return {"paid": False, "message": _("Your payment is processing. We'll email you when it's confirmed.")}
	return {"paid": False, "message": _("The payment didn't go through. Please try again or use another card.")}


def record_payment(intent) -> str | None:
	"""Mark the order's Payment Request paid, once, after checking the amount with Stripe's record."""
	order_name = (intent.metadata or {}).get("sales_order")
	if not order_name or not frappe.db.exists("Sales Order", order_name):
		return None
	order = frappe.get_doc("Sales Order", order_name)
	if intent.currency != order.currency.lower():
		frappe.log_error(title="Stripe payment currency does not match the order", message=str(intent.id))
		return None
	request = payment_request(order)
	if not request:
		frappe.log_error(title="Stripe payment has no Payment Request", message=f"{intent.id} {order_name}")
		return None
	if request.status == "Paid":
		return request.name
	if intent.amount_received < minor_units(request.grand_total, order.currency):
		frappe.log_error(title="Stripe payment is less than the amount due", message=f"{intent.id} {order_name}")
		return None
	from shop.storefront.checkout import elevated

	with elevated():
		request.reload()
		request.flags.ignore_permissions = True
		request.run_method("on_payment_authorized", "Completed")
		order.add_comment("Info", _("Paid by card through Stripe ({0}).").format(intent.id))
	return request.name


def confirmation(order, token: str) -> str:
	return frappe.utils.get_url(store_url(f"order-confirmation/{order.name}?token={token}"))


@frappe.whitelist(allow_guest=True, methods=["POST"])
def webhook():
	"""Stripe events. Only payment_intent.succeeded is acted on; the signature is checked first."""
	secret = frappe.get_cached_doc("Shop Settings").get_password("stripe_webhook_secret", raise_exception=False)
	payload = frappe.request.get_data() if frappe.request else b""
	if not secret or not valid_signature(payload, frappe.get_request_header("Stripe-Signature"), secret):
		raise frappe.PermissionError
	event = json.loads(payload or b"{}")
	if event.get("type") != "payment_intent.succeeded":
		return {"received": True}
	# never trust the event body for money: fetch the PaymentIntent from Stripe
	intent_id = ((event.get("data") or {}).get("object") or {}).get("id")
	if intent_id:
		intent = client().payment_intents.retrieve(intent_id)
		if intent.status == "succeeded":
			record_payment(intent)
	return {"received": True}


def valid_signature(payload: bytes, header: str | None, secret: str, now: float | None = None) -> bool:
	"""Stripe-Signature: t=<timestamp>,v1=<hex HMAC-SHA256 of "<timestamp>.<payload>">."""
	if not header:
		return False
	parts = {}
	for item in header.split(","):
		key, _sep, value = item.strip().partition("=")
		parts.setdefault(key, []).append(value)
	timestamp = (parts.get("t") or [""])[0]
	if not timestamp.isdigit() or abs((now or time.time()) - int(timestamp)) > WEBHOOK_TOLERANCE_SECONDS:
		return False
	expected = hmac.new(secret.encode(), f"{timestamp}.".encode() + payload, hashlib.sha256).hexdigest()
	return any(hmac.compare_digest(expected, signature) for signature in parts.get("v1", []))
