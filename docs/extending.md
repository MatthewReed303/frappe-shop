# Extending Shop

Other apps plug into Shop through hooks in their `hooks.py`.

## Shipping rates

A shipping app offers live rates at checkout by listing a function in
`shop_shipping_rate_providers`:

```python
shop_shipping_rate_providers = ["my_carrier.rates.checkout_rates"]
```

The function receives a context and returns the options it can offer:

```python
def checkout_rates(context):
	# context.cart_items: [{"item_code", "qty", "rate"}]
	# context.address: the delivery address, with "country" (name) and "country_code"
	# context.subtotal, context.company, context.currency (the company currency)
	return [
		{
			"id": "courier-overnight",  # unique among your options
			"label": "Courier, overnight",
			"amount": 12.5,  # in the company currency
			"tax_included": False,  # True when the amount includes the store's own tax
			"carrier": "Courier Co",
			"service": "Overnight",
			"eta": "1-2 days",
			"meta": {},  # anything you need again when the order ships
		}
	]
```

- Return `{"options": [...], "messages": ["..."]}` to tell the shopper why there are no
  rates (an item you can't carry overseas, say).
- Return `None` when your app is switched off for this site.
- Raise `frappe.ValidationError` with a shopper-friendly message when the address can't be
  served. Other errors are logged and your options are left out.

Shop adds its own flat rate (Shop Settings) to your options. When no provider is active
and there is no flat rate, shipping is free. Provider rates are cached on the cart for 30
minutes, keyed by the items and address, so cache only successful answers on your side.
The chosen option is stored on the Sales Order (`shop_shipping_option`), with its `meta`.

## Fulfillment

A fulfillment app registers a provider class (see `shop.fulfillment.provider`):

```python
shop_fulfillment_providers = {"my_carrier": "my_carrier.fulfillment.MyCarrierProvider"}
```

`shop_carrier_tracking_urls` maps a carrier name to tracking URL templates for shipments
made by hand.

## Site themes

A site app that builds its own storefront from one of Shop's theme generators (see
[Theming](theming.md#site-themes)) lists the components it generates, so Shop keeps their
store links right when the store's route prefix changes:

```python
shop_theme_components = ["acme-navbar", "acme-hero", "acme-product-card"]
```
