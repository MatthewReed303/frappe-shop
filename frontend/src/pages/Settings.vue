<template>
	<div class="mx-auto max-w-5xl px-6 py-8">
		<h1 class="text-xl font-semibold text-ink-gray-9">Settings</h1>

		<CatalogListState
			:loading="settings.loading && !settings.data"
			:error="settings.error"
			empty-title=""
			@retry="settings.reload()"
		/>

		<div v-if="settings.data" class="mt-6 space-y-6">
			<CatalogSection title="Store" description="How your store shows up to customers.">
				<FormControl v-model="store.store_name" label="Store name" class="max-w-sm" />
				<CatalogImageInput v-model="store.store_logo" label="Logo" />
				<div class="grid max-w-sm grid-cols-2 gap-4">
					<FormControl :model-value="data.currency" label="Currency" disabled />
					<FormControl :model-value="data.company" label="Company" disabled />
				</div>
				<p class="text-p-sm text-ink-gray-5">
					Currency and company come from your ERPNext setup and cannot be changed here.
				</p>
				<template #footer>
					<Button variant="solid" :loading="saving === 'store'" @click="saveSection('store', store)">
						Save
					</Button>
				</template>
			</CatalogSection>

			<CatalogSection title="Payments" description="Choose how customers pay at checkout.">
				<Switch
					v-model="payments.enable_cod"
					label="Cash on Delivery"
					description="Let customers pay in cash when their order arrives."
				/>
				<div class="flex max-w-3xl flex-col gap-3">
					<div class="text-base font-medium text-ink-gray-8">Online payment gateways</div>
					<div
						v-for="(row, index) in payments.payment_gateways"
						:key="index"
						class="grid grid-cols-6 gap-3 rounded border border-outline-gray-2 p-3"
					>
						<FormControl
							v-model="row.payment_gateway"
							type="select"
							label="Payment gateway"
							:options="gatewayOptions"
							class="col-span-3"
						/>
						<FormControl
							v-model="row.label"
							label="Label at checkout"
							placeholder="e.g. Card or PayPal"
							class="col-span-3"
						/>
						<FormControl
							v-model="row.fee_label"
							label="Fee name"
							placeholder="Card fee"
							class="col-span-2"
						/>
						<FormControl
							v-model.number="row.fee_percent"
							type="number"
							label="Fee %"
							class="col-span-2"
						/>
						<FormControl
							v-model.number="row.fee_fixed"
							type="number"
							:label="`Fixed fee (${data.company_currency})`"
							class="col-span-2"
						/>
						<FormControl
							v-model.number="row.fee_percent_international"
							type="number"
							label="Fee % (international)"
							class="col-span-2"
						/>
						<FormControl
							v-model.number="row.fee_fixed_international"
							type="number"
							:label="`Fixed fee, international (${data.company_currency})`"
							class="col-span-2"
						/>
						<Button
							variant="ghost"
							class="col-span-2 self-end"
							@click="payments.payment_gateways.splice(index, 1)"
						>
							Remove
						</Button>
					</div>
					<div class="flex items-center gap-3">
						<Button :disabled="!gatewayOptions.length" @click="addGateway">Add payment gateway</Button>
						<a
							href="/app/payment-gateway"
							target="_blank"
							class="inline-flex items-center gap-1 text-sm text-ink-gray-6 underline hover:text-ink-gray-8"
						>
							Set up Stripe, PayPal and other gateways in Desk (Payment Gateways)
							<LucideExternalLink class="size-3.5" />
						</a>
					</div>
					<p class="text-p-sm text-ink-gray-5">
						Each gateway is a choice at checkout. An order is charged in the shopper's currency when the
						gateway has a Payment Gateway Account in it, otherwise in the company's currency in ERPNext
						({{ data.company_currency }}). Fixed fees are in that currency. International fees are used
						for orders shipped outside the company's country ({{ data.company_country }}).
					</p>
				</div>
				<FormControl
					v-model="payments.card_fee_item"
					type="select"
					label="Payment fee item"
					description="A gateway's fee is added to the order as a line with this item."
					:options="feeItemOptions('No payment fee')"
					class="max-w-sm"
				/>
				<NewServiceItem suggestion="Payment fee" @created="(name) => useItem('card_fee_item', name)" />
				<Switch
					v-model="payments.enable_bank_transfer"
					label="Bank transfer"
					description="Customers pay into one of your bank accounts, with the order number as reference."
				/>
				<template v-if="payments.enable_bank_transfer">
					<FormControl
						v-model.number="payments.bank_transfer_days"
						type="number"
						label="Days to pay by bank transfer"
						class="max-w-sm"
					/>
					<div class="flex max-w-3xl flex-col gap-3">
						<div
							v-for="(row, index) in payments.bank_accounts"
							:key="index"
							class="grid grid-cols-6 gap-3 rounded border border-outline-gray-2 p-3"
						>
							<FormControl
								v-model="row.bank_account"
								type="select"
								label="Bank account"
								:options="bankAccountOptions"
								class="col-span-3"
							/>
							<FormControl
								v-model="row.label"
								label="Label"
								placeholder="e.g. US dollars"
								class="col-span-3"
							/>
							<FormControl
								v-model="row.branch_code_label"
								label="Branch code name"
								placeholder="e.g. BSB, Sort code"
								class="col-span-2"
							/>
							<FormControl
								v-model.number="row.conversion_fee_percent"
								type="number"
								label="Conversion fee %"
								class="col-span-2"
							/>
							<FormControl
								v-model.number="row.local_transfer_fee"
								type="number"
								:label="`Local transfer fee (${bankCurrency(row)})`"
								class="col-span-2"
							/>
							<FormControl
								v-if="bankHasSwift(row)"
								v-model.number="row.swift_transfer_fee"
								type="number"
								:label="`Swift transfer fee (${bankCurrency(row)})`"
								class="col-span-2"
							/>
							<FormControl
								v-model="row.instructions"
								type="textarea"
								label="Instructions"
								class="col-span-5"
							/>
							<Button
								variant="ghost"
								class="col-span-1 self-end"
								@click="payments.bank_accounts.splice(index, 1)"
							>
								Remove
							</Button>
						</div>
						<div>
							<Button @click="addBankAccount">Add bank account</Button>
						</div>
					</div>
					<p class="text-p-sm text-ink-gray-5">
						Transfer fees are what the bank takes from a payment, in the account's currency, and are
						added to the order. Swift transfers are offered when the account's Bank has a SWIFT code.
					</p>
					<div class="grid max-w-lg grid-cols-2 gap-4">
						<FormControl
							v-model="payments.transfer_fee_item"
							type="select"
							label="Transfer fee item"
							:options="feeItemOptions('No transfer fees')"
						/>
						<FormControl
							v-model="payments.conversion_fee_item"
							type="select"
							label="Currency conversion item"
							:options="feeItemOptions('No conversion fees')"
						/>
						<NewServiceItem suggestion="Bank transfer fee" @created="(name) => useItem('transfer_fee_item', name)" />
						<NewServiceItem
							suggestion="Currency conversion"
							@created="(name) => useItem('conversion_fee_item', name)"
						/>
						<FormControl
							v-model="payments.bank_charges_account"
							type="select"
							label="Bank charges account"
							description="Where a fee the bank reports taking is booked."
							:options="expenseAccountOptions"
						/>
					</div>
					<a
						href="/app/bank-account"
						target="_blank"
						class="inline-flex items-center gap-1 text-sm text-ink-gray-6 underline hover:text-ink-gray-8"
					>
						Manage bank accounts in Desk
						<LucideExternalLink class="size-3.5" />
					</a>
				</template>
				<template #footer>
					<Button
						variant="solid"
						:loading="saving === 'payments'"
						@click="saveSection('payments', payments)"
					>
						Save
					</Button>
				</template>
			</CatalogSection>

			<CatalogSection title="Shipping" description="Delivery charges applied at checkout.">
				<div class="grid max-w-sm grid-cols-2 gap-4">
					<FormControl
						v-model.number="shipping.flat_shipping_rate"
						type="number"
						:label="`Flat rate (${data.currency})`"
						description="0 means shipping is free."
					/>
					<FormControl
						v-model.number="shipping.free_shipping_above"
						type="number"
						:label="`Free shipping above (${data.currency})`"
						description="0 turns this threshold off."
					/>
				</div>
				<FormControl
					v-model="shipping.shipping_account"
					type="select"
					label="Shipping account"
					:options="accountOptions"
					class="max-w-sm"
				/>
				<FormControl
					v-model="shipping.shipping_item"
					type="select"
					label="Shipping item"
					:options="itemOptions"
					class="max-w-sm"
				/>
				<NewServiceItem suggestion="Shipping" @created="(name) => useItem('shipping_item', name)" />
				<p class="text-p-sm text-ink-gray-5">
					Shipping is charged as the shipping item's line, or on the shipping account when no item
					is set. One of them is required for shipping charges to apply on orders.
				</p>
				<template #footer>
					<Button
						variant="solid"
						:loading="saving === 'shipping'"
						@click="saveSection('shipping', shipping)"
					>
						Save
					</Button>
				</template>
			</CatalogSection>

			<CatalogSection
				title="Fulfillment"
				description="The warehouses you sell from and who ships from each, first to last."
			>
				<div class="flex max-w-3xl flex-col gap-3">
					<div
						v-for="(row, index) in fulfillment.fulfillment_locations"
						:key="index"
						class="grid grid-cols-6 gap-3 rounded border border-outline-gray-2 p-3"
					>
						<FormControl
							v-model="row.warehouse"
							type="select"
							label="Warehouse"
							:options="data.warehouses || []"
							class="col-span-3"
						/>
						<FormControl
							v-model="row.display_name"
							label="Name customers see"
							placeholder="e.g. Auckland store"
							class="col-span-3"
						/>
						<Switch
							v-model="row.show_stock"
							label="Show its stock on product pages"
							class="col-span-3 self-end"
						/>
						<FormControl
							v-model="row.fulfillment_provider"
							type="select"
							label="Shipped by"
							:options="providerOptions"
							class="col-span-3"
						/>
						<FormControl
							v-model="row.ship_from_address"
							type="select"
							label="Ship from"
							:options="[{ label: 'The warehouse\'s address, else the provider\'s', value: '' }, ...(data.addresses || [])]"
							class="col-span-3"
						/>
						<Switch
							v-model="row.auto_send"
							label="Send paid orders automatically"
							class="col-span-2 self-end"
						/>
						<p v-if="row.warning" class="col-span-6 text-p-sm text-ink-amber-8">{{ row.warning }}</p>
						<div class="col-span-1 flex items-end justify-end gap-1">
							<Button
								variant="ghost"
								:disabled="index === 0"
								:aria-label="'Move up'"
								@click="moveLocation(index, -1)"
							>
								↑
							</Button>
							<Button variant="ghost" @click="fulfillment.fulfillment_locations.splice(index, 1)">
								Remove
							</Button>
						</div>
					</div>
					<div>
						<Button @click="addLocation">Add warehouse</Button>
					</div>
				</div>
				<p class="text-p-sm text-ink-gray-5">
					An order takes each item from the first warehouse that has all of it, and is sent from each
					warehouse as its own parcel. At checkout each parcel is priced by who ships it, and the
					customer picks a service for each.
				</p>
				<p class="text-p-sm text-ink-gray-5">
					Providers marked as needing setup have to be connected before orders can reach them.
				</p>
				<a
					href="/app/shop-amazon-fulfillment-settings"
					target="_blank"
					class="inline-flex items-center gap-1 text-sm text-ink-gray-6 underline hover:text-ink-gray-8"
				>
					Set up Amazon Multi-Channel Fulfillment in Desk
					<LucideExternalLink class="size-3.5" />
				</a>
				<template #footer>
					<Button
						variant="solid"
						:loading="saving === 'fulfillment'"
						@click="saveSection('fulfillment', fulfillment)"
					>
						Save
					</Button>
				</template>
			</CatalogSection>

			<CatalogSection title="Catalog" description="Pricing, stock and tax defaults.">
				<div class="grid max-w-lg grid-cols-2 gap-4">
					<FormControl
						v-model="catalog.price_list"
						type="select"
						label="Price list"
						:options="listOptions(data.price_lists)"
					/>
					<FormControl
						v-model.number="catalog.low_stock_threshold"
						type="number"
						label="Low stock threshold"
					/>
					<FormControl
						v-model="catalog.tax_template"
						type="select"
						label="Tax template"
						:options="taxOptions"
					/>
				</div>
				<Switch
					v-model="catalog.allow_out_of_stock"
					label="Allow out of stock orders"
					description="Customers can order items with zero stock."
				/>
				<Switch
					v-model="catalog.prices_include_tax"
					label="Prices include tax"
					description="Storefront prices are treated as tax inclusive."
				/>
				<Switch
					v-model="catalog.show_prices_with_tax"
					label="Show prices with tax"
					description="Show prices with the tax template's tax, and the price without tax under them."
				/>
				<Switch
					v-model="catalog.other_currencies_with_tax"
					label="Show other currencies with tax"
:description="`Off: prices in another currency are shown without tax, as for a buyer outside ${data.company_country || 'the company\'s country'}. Checkout always charges the tax for the delivery address.`"
				/>
				<FormControl
					v-model="catalog.export_tax_template"
					type="select"
					label="Export tax template"
:description="`Taxes for orders shipped outside ${data.company_country || 'the company\'s country'}, when no Tax Rule for the shopping cart matches.`"
					:options="taxOptions"
					class="max-w-sm"
				/>
				<template #footer>
					<Button
						variant="solid"
						:loading="saving === 'catalog'"
						@click="saveSection('catalog', catalog)"
					>
						Save
					</Button>
				</template>
			</CatalogSection>

			<CatalogSection title="Orders" description="What happens to orders after checkout.">
				<FormControl
					v-model="orders.order_naming_series"
					label="Order naming series"
					description="A short Sales Order naming series (e.g. SO-.#####) so order numbers fit bank references. Add it to Sales Order in Document Naming Settings first."
					class="max-w-sm"
				/>
				<Switch
					v-model="orders.round_order_totals"
					label="Round order totals"
					description="ERPNext's Rounded Total: the total is rounded to whole currency units and the customer pays the rounded amount. Off charges the exact amount."
				/>
				<Switch
					v-model="orders.invoice_paid_orders"
					label="Invoice paid orders"
					description="Make the Sales Invoice when an order is paid in full, and email it to the customer."
				/>
				<Switch
					v-model="orders.cancel_unpaid_orders"
					label="Cancel unpaid orders"
					description="Cancel online and bank transfer orders not paid by their due date, and tell the customer."
				/>
				<FormControl
					v-if="orders.cancel_unpaid_orders"
					v-model.number="orders.online_payment_days"
					type="number"
					label="Days to pay online"
					class="max-w-sm"
				/>
				<div class="grid max-w-lg grid-cols-2 gap-4">
					<FormControl
						v-model.number="orders.payment_reminder_hours"
						type="number"
						label="Remind unpaid online orders after (hours)"
						description="0 sends no reminder."
					/>
					<FormControl
						v-model.number="orders.return_window_days"
						type="number"
						label="Return window (days)"
						description="Days after shipping customers can ask for a return. 0 turns it off."
					/>
				</div>
				<Switch
					v-model="orders.send_shipping_emails"
					label="Email shipping updates"
					description="Email the customer when their order ships and when it is delivered."
				/>
				<template #footer>
					<Button variant="solid" :loading="saving === 'orders'" @click="saveSection('orders', orders)">
						Save
					</Button>
				</template>
			</CatalogSection>

			<CatalogSection title="Accounts" description="Terms, trade accounts and quotes.">
				<Switch
					v-model="accounts.require_terms"
					label="Require terms"
					description="Customers accept your terms when they sign up, order or accept a quote."
				/>
				<div v-if="accounts.require_terms" class="grid max-w-lg grid-cols-2 gap-4">
					<FormControl
						v-model="accounts.terms_and_conditions"
						type="select"
						label="Terms and conditions"
						:options="listOptions(data.terms_records)"
					/>
					<FormControl
						v-model="accounts.terms_url"
						label="Terms page URL"
						description="Leave empty to use the store's terms page."
					/>
					<a
						:href="
							accounts.terms_and_conditions
								? `/app/terms-and-conditions/${encodeURIComponent(accounts.terms_and_conditions)}`
								: '/app/terms-and-conditions/new'
						"
						target="_blank"
						class="col-span-2 inline-flex items-center gap-1 text-sm text-ink-gray-6 underline hover:text-ink-gray-8"
					>
						{{ accounts.terms_and_conditions ? 'Edit these terms in Desk' : 'Write your terms in Desk (Terms and Conditions)' }}
						<LucideExternalLink class="size-3.5" />
					</a>
				</div>
				<FormControl
					v-model="accounts.new_customer_group"
					type="select"
					label="Customer group for new customers"
					description="ERPNext's default Customer Group (Selling Settings): customers who sign up or order online join it, with its prices and pricing rules."
					:options="[{ label: 'Not set', value: '' }, ...listOptions(data.customer_groups)]"
					class="max-w-sm"
				/>
				<FormControl
					v-model="accounts.trade_customer_group"
					type="select"
					label="Trade customer group"
					description="Approved trade customers join this group, with its prices and payment terms."
					:options="[{ label: 'No trade accounts', value: '' }, ...listOptions(data.customer_groups)]"
					class="max-w-sm"
				/>
				<Switch
					v-if="accounts.trade_customer_group"
					v-model="accounts.email_statements"
					label="Email monthly statements"
					description="Each trade customer gets a statement every month."
				/>
				<TradeDiscounts v-if="data.trade_customer_group" />
				<Switch
					v-model="accounts.allow_quote_requests"
					label="Quote requests"
					description="Shoppers can ask for a quote at checkout instead of ordering."
				/>
				<FormControl
					v-if="accounts.allow_quote_requests"
					v-model.number="accounts.quote_valid_days"
					type="number"
					label="Quotes valid for (days)"
					class="max-w-sm"
				/>
				<template #footer>
					<Button variant="solid" :loading="saving === 'accounts'" @click="saveSection('accounts', accounts)">
						Save
					</Button>
				</template>
			</CatalogSection>

			<CatalogSection title="Storefront" description="Your storefront pages are built with Builder.">
				<template #header-action>
					<Button @click="openBuilder">
						<template #prefix><LucideExternalLink class="size-3.5" /></template>
						Edit in Builder
					</Button>
				</template>
				<StorefrontThemes @applied="settings.reload()" />
			</CatalogSection>
		</div>
	</div>
</template>

<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { FormControl, Switch, call, createResource, toast } from 'frappe-ui'

import LucideExternalLink from '~icons/lucide/external-link'

import CatalogImageInput from '@/components/CatalogImageInput.vue'
import CatalogListState from '@/components/CatalogListState.vue'
import CatalogSection from '@/components/CatalogSection.vue'
import NewServiceItem from '@/components/NewServiceItem.vue'
import TradeDiscounts from '@/components/TradeDiscounts.vue'
import StorefrontThemes from '@/components/StorefrontThemes.vue'

const saving = ref('')

const store = reactive({ store_name: '', store_logo: '' })
const payments = reactive({
	enable_cod: true,
	payment_gateways: [] as GatewayRow[],
	card_fee_item: '',
	enable_bank_transfer: false,
	bank_transfer_days: 7,
	bank_accounts: [] as BankAccountRow[],
	transfer_fee_item: '',
	conversion_fee_item: '',
	bank_charges_account: '',
})
const shipping = reactive({
	flat_shipping_rate: 0,
	free_shipping_above: 0,
	shipping_account: '',
	shipping_item: '',
})
const orders = reactive({
	round_order_totals: true,
	invoice_paid_orders: false,
	cancel_unpaid_orders: false,
	online_payment_days: 2,
	order_naming_series: '',
	payment_reminder_hours: 1,
	return_window_days: 14,
	send_shipping_emails: false,
})
const accounts = reactive({
	new_customer_group: '',
	require_terms: false,
	terms_and_conditions: '',
	terms_url: '',
	trade_customer_group: '',
	email_statements: false,
	allow_quote_requests: false,
	quote_valid_days: 30,
})
type LocationRow = {
	warehouse: string
	display_name: string
	show_stock: boolean
	fulfillment_provider: string
	ship_from_address: string
	auto_send: boolean
	warning?: string | null
}

const fulfillment = reactive({ fulfillment_locations: [] as LocationRow[] })

function addLocation() {
	const used = fulfillment.fulfillment_locations.map((row) => row.warehouse)
	const next = (data.value.warehouses || []).find((option: { value: string }) => !used.includes(option.value))
	fulfillment.fulfillment_locations.push({
		warehouse: next?.value || '',
		display_name: '',
		show_stock: false,
		fulfillment_provider: 'manual',
		ship_from_address: '',
		auto_send: false,
	})
}

function moveLocation(index: number, step: number) {
	const rows = fulfillment.fulfillment_locations
	const [row] = rows.splice(index, 1)
	rows.splice(index + step, 0, row)
}
const catalog = reactive({
	price_list: '',
	low_stock_threshold: 0,
	tax_template: '',
	export_tax_template: '',
	allow_out_of_stock: false,
	prices_include_tax: false,
	show_prices_with_tax: false,
	other_currencies_with_tax: false,
})

const settings = createResource({
	url: 'shop.api.settings.get_settings',
	auto: true,
})

const data = computed(() => settings.data || {})

watch(
	() => settings.data,
	(doc) => {
		if (doc) hydrate(doc)
	},
	{ immediate: true },
)

function hydrate(doc: Record<string, any>) {
	Object.assign(store, { store_name: doc.store_name || '', store_logo: doc.store_logo || '' })
	Object.assign(payments, {
		enable_cod: !!doc.enable_cod,
		payment_gateways: (doc.payment_gateways || []).map((row: GatewayRow) => ({ ...row })),
		card_fee_item: doc.card_fee_item || '',
		enable_bank_transfer: !!doc.enable_bank_transfer,
		bank_transfer_days: doc.bank_transfer_days || 7,
		bank_accounts: (doc.bank_accounts || []).map((row: BankAccountRow) => ({ ...row })),
		transfer_fee_item: doc.transfer_fee_item || '',
		conversion_fee_item: doc.conversion_fee_item || '',
		bank_charges_account: doc.bank_charges_account || '',
	})
	Object.assign(shipping, {
		flat_shipping_rate: doc.flat_shipping_rate || 0,
		free_shipping_above: doc.free_shipping_above || 0,
		shipping_account: doc.shipping_account || '',
		shipping_item: doc.shipping_item || '',
	})
	Object.assign(orders, {
		invoice_paid_orders: !!doc.invoice_paid_orders,
		cancel_unpaid_orders: !!doc.cancel_unpaid_orders,
		online_payment_days: doc.online_payment_days || 2,
		order_naming_series: doc.order_naming_series || '',
		round_order_totals: !!doc.round_order_totals,
		payment_reminder_hours: doc.payment_reminder_hours ?? 1,
		return_window_days: doc.return_window_days ?? 14,
		send_shipping_emails: !!doc.send_shipping_emails,
	})
	Object.assign(accounts, {
		require_terms: !!doc.require_terms,
		terms_and_conditions: doc.terms_and_conditions || '',
		terms_url: doc.terms_url || '',
		trade_customer_group: doc.trade_customer_group || '',
		new_customer_group: doc.new_customer_group || '',
		email_statements: !!doc.email_statements,
		allow_quote_requests: !!doc.allow_quote_requests,
		quote_valid_days: doc.quote_valid_days || 30,
	})
	Object.assign(fulfillment, {
		fulfillment_locations: (doc.fulfillment_locations || []).map((row: LocationRow) => ({
			...row,
			ship_from_address: row.ship_from_address || '',
			display_name: row.display_name || '',
			show_stock: !!row.show_stock,
			auto_send: !!row.auto_send,
		})),
	})

	Object.assign(catalog, {
		price_list: doc.price_list || '',
		low_stock_threshold: doc.low_stock_threshold || 0,
		tax_template: doc.tax_template || '',
		export_tax_template: doc.export_tax_template || '',
		allow_out_of_stock: !!doc.allow_out_of_stock,
		prices_include_tax: !!doc.prices_include_tax,
		show_prices_with_tax: !!doc.show_prices_with_tax,
		other_currencies_with_tax: !!doc.other_currencies_with_tax,
	})
}

type GatewayRow = {
	payment_gateway: string
	label: string
	fee_label: string
	fee_percent: number
	fee_fixed: number
	fee_percent_international: number
	fee_fixed_international: number
}

const gatewayOptions = computed(() => {
	const currencies: Record<string, string[]> = {}
	for (const a of (data.value.gateway_accounts || []) as { payment_gateway: string; currency: string }[]) {
		;(currencies[a.payment_gateway] ||= []).push(a.currency)
	}
	return Object.entries(currencies).map(([gateway, list]) => ({
		label: `${gateway} (${list.join(', ')})`,
		value: gateway,
	}))
})

function addGateway() {
	const used = payments.payment_gateways.map((row) => row.payment_gateway)
	const next = gatewayOptions.value.find((option) => !used.includes(option.value))
	payments.payment_gateways.push({
		payment_gateway: next?.value || '',
		label: '',
		fee_label: '',
		fee_percent: 0,
		fee_fixed: 0,
		fee_percent_international: 0,
		fee_fixed_international: 0,
	})
}

const accountOptions = computed(() => [
	{ label: 'No shipping account', value: '' },
	...listOptions(data.value.income_accounts),
])

const providerOptions = computed(() =>
	(data.value.fulfillment_providers || []).map(
		(provider: { key: string; label: string; configured: boolean }) => ({
			label: provider.configured ? provider.label : `${provider.label} (needs setup)`,
			value: provider.key,
		}),
	),
)

type BankAccountRow = {
	bank_account: string
	label: string
	branch_code_label: string
	conversion_fee_percent: number
	local_transfer_fee: number
	swift_transfer_fee: number
	instructions: string
}

type CompanyBankAccount = { name: string; currency: string | null; swift: boolean }

const bankAccountOptions = computed(() =>
	(data.value.company_bank_accounts || []).map((account: CompanyBankAccount) => ({
		label: account.currency ? `${account.name} (${account.currency})` : account.name,
		value: account.name,
	})),
)

function companyBankAccount(row: BankAccountRow): CompanyBankAccount | undefined {
	return (data.value.company_bank_accounts || []).find(
		(account: CompanyBankAccount) => account.name === row.bank_account,
	)
}

function bankCurrency(row: BankAccountRow) {
	return companyBankAccount(row)?.currency || data.value.currency
}

function bankHasSwift(row: BankAccountRow) {
	return !!companyBankAccount(row)?.swift
}

function addBankAccount() {
	payments.bank_accounts.push({
		bank_account: '',
		label: '',
		branch_code_label: '',
		conversion_fee_percent: 0,
		local_transfer_fee: 0,
		swift_transfer_fee: 0,
		instructions: '',
	})
}

function feeItemOptions(none: string) {
	return [{ label: none, value: '' }, ...listOptions(data.value.service_items)]
}

const expenseAccountOptions = computed(() => [
	{ label: 'Not set', value: '' },
	...listOptions(data.value.expense_accounts),
])

const itemOptions = computed(() => [
	{ label: 'No shipping item', value: '' },
	...listOptions(data.value.service_items),
])

const taxOptions = computed(() => [
	{ label: 'No tax template', value: '' },
	...listOptions(data.value.tax_templates),
])

// a new service item joins the choices and is picked; the section is saved as usual
function useItem(field: 'card_fee_item' | 'transfer_fee_item' | 'conversion_fee_item' | 'shipping_item', name: string) {
	settings.data.service_items = [...(settings.data.service_items || []), name]
	if (field === 'shipping_item') shipping.shipping_item = name
	else payments[field] = name
	toast.success(`${name} created. Save to use it.`)
}

function listOptions(names?: string[]) {
	return (names || []).map((name) => ({ label: name, value: name }))
}

async function saveSection(section: string, payload: Record<string, any>) {
	saving.value = section
	try {
		settings.data = await call('shop.api.settings.save_settings', {
			payload: normalize(payload),
		})
		toast.success('Settings saved')
	} catch (error) {
		const messages = (error as { messages?: string[] }).messages
		toast.error(messages?.[0] || 'Could not save settings')
	} finally {
		saving.value = ''
	}
}

function normalize(payload: Record<string, any>) {
	// Empty select values mean "unset" for link fields.
	return Object.fromEntries(
		Object.entries(payload).map(([key, value]) => [key, value === '' ? null : value]),
	)
}

function openBuilder() {
	window.open('/builder', '_blank')
}
</script>
