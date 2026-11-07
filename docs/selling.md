# Selling

Everything the store sells with is an ERPNext document, so the desk, reports and accounts
see exactly what the storefront did. This page lists the store settings (Shop Settings, also
in the admin at `/shop/settings`) and the ERPNext records behind them. New behaviour is off
until it is turned on.

## Prices and tax

- Prices come from the store's Price List. A signed-in customer gets their own: the Price
  List of their Customer or Customer Group, with ERPNext's Pricing Rules applied (trade
  discounts, quantity breaks).
- Checkout builds the Sales Order it will place and lets ERPNext price, tax and total it, so
  the total shown is the total charged. Coupons, Item Tax Templates and rounding follow
  ERPNext.
- Taxes: the store's Tax Template, or the ERPNext Tax Rule marked *Use for Shopping Cart*
  that matches the delivery address and customer. Orders shipped outside the company's
  country use the **Export Tax Template** when no rule matches.
- **Prices Include Tax**: the price list includes the Tax Template's tax (its rows must be
  *Included in Print Rate*).
- **Show Prices With Tax**: product, cart and checkout prices are shown with the tax, and
  the price without tax under them.

## Paying

- **Cash on Delivery**.
- **Online**: any gateway of the payments app, through a Payment Gateway Account. The
  **Online Payment Label** names it at checkout. A **Card Fee** (percent and fixed, with
  their own international rates) can be added as a line: what the store keeps of it covers
  the provider's charge on the final total.
- **Bank transfer**: the store's ERPNext Bank Accounts, one per currency, listed in Shop
  Settings. The shopper sees the account details, the amount and the order number as the
  reference. An account in another currency can add a currency conversion fee.
- **Pay on account**: customers with *Pay on Account at the Store* and Payment Terms are
  invoiced straight away. ERPNext's credit limit applies.

Shipping, card fees and conversion fees are order lines (the Shipping Item, Card Fee Item and
Currency Conversion Item), so they show on the order and invoice like any other line.

## After checkout

- **Invoice Paid Orders**: once an order is paid in full, its Sales Invoice is made with
  ERPNext's own *Make Sales Invoice*, the payment allocated, and emailed to the customer.
- **Cancel Unpaid Orders**: online and bank transfer orders not paid by their due date
  (**Days to Pay Online**, **Days to Pay** for bank transfers) are cancelled, and the
  customer is told.
- **Email Shipping Updates**: the customer hears when their order ships and when it is
  delivered.
- Store emails are Frappe Email Templates named *Shop: ...*, made once and never
  overwritten, so they can be edited in the desk.

## Customer accounts

- **Require Terms**: shoppers accept the store's ERPNext Terms and Conditions when they sign
  up, order or accept a quote. What they accepted and when is kept on the order or user,
  and the terms print on the order and invoice.
- **Trade accounts**: businesses apply on the store's trade page. Each application is an
  ERPNext Lead (UTM Source *Store Trade Application*). Staff approve or decline it from the
  Lead: approving makes the Customer (or updates the applicant's own) in the **Trade
  Customer Group**, with Payment Terms, credit limit and pay on account as chosen.
- **Quote Requests**: shoppers can ask for a quote at checkout. It arrives as a draft
  Quotation; staff submit it and send it from the Quotation, and the customer accepts it
  online, which places the order.
- **Email Monthly Statements**: the trade Customer Group gets an ERPNext Process Statement
  Of Accounts each month. Customers can also download their statement from their account.

## Currencies

Shoppers can see approximate prices in any currency the store has a bank account in, at
ERPNext's exchange rate, and pay by bank transfer in it. Online and cash payments are in the
company currency.
