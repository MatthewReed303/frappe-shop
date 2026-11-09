<template>
	<div class="space-y-3">
		<div>
			<div class="text-sm font-medium text-ink-gray-8">Trade discounts</div>
			<p class="text-p-sm text-ink-gray-5">
				Percent off an Item Group for the trade group, kept as ERPNext Pricing Rules. Rules by item or quantity,
				and customer prices, are set up in Desk.
			</p>
		</div>
		<p v-if="trade.loading && !trade.data" class="text-p-sm text-ink-gray-5">Loading…</p>
		<p v-else-if="trade.error" class="text-p-sm text-ink-red-4">
			Couldn't load the trade discounts.
			<button type="button" class="underline" @click="trade.reload()">Try again</button>
		</p>
		<p v-else-if="!rules.length" class="text-p-sm text-ink-gray-5">No trade discounts yet.</p>
		<div
			v-for="rule in rules"
			:key="rule.name"
			class="flex max-w-lg items-center justify-between rounded border border-outline-gray-1 px-3 py-2 text-base"
		>
			<span class="text-ink-gray-8">{{ rule.item_group }}</span>
			<span class="flex items-center gap-3">
				<span class="text-ink-gray-7">{{ rule.discount_percentage }}% off</span>
				<Button variant="ghost" theme="red" :loading="removing === rule.name" @click="remove(rule)">
					Remove
				</Button>
			</span>
		</div>
		<div class="flex max-w-lg items-end gap-2">
			<FormControl
				v-model="itemGroup"
				type="select"
				label="Item Group"
				class="flex-1"
				:options="[{ label: 'Select', value: '' }, ...groupOptions]"
			/>
			<FormControl v-model.number="percent" type="number" label="% off" class="w-24" />
			<Button :disabled="!itemGroup || !percent" @click="add">Add</Button>
		</div>
	</div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { Button, FormControl, call, createResource, dialog, toast } from 'frappe-ui'

interface Rule {
	name: string
	item_group: string
	discount_percentage: number
}

const trade = createResource({ url: 'shop.api.trade.get_trade', auto: true })
const updated = ref<Rule[] | null>(null)
const rules = computed<Rule[]>(() => updated.value || trade.data?.discounts || [])
const groupOptions = computed(() =>
	(trade.data?.item_groups || []).map((name: string) => ({ label: name, value: name })),
)

const itemGroup = ref('')
const percent = ref<number | null>(null)

async function add() {
	try {
		updated.value = await call('shop.api.trade.set_discount', { item_group: itemGroup.value, percent: percent.value })
		itemGroup.value = ''
		percent.value = null
		toast.success('Trade discount saved')
	} catch (error) {
		const messages = (error as { messages?: string[] }).messages
		toast.error(messages?.[0] || 'Could not save the discount')
	}
}

const removing = ref('')

function remove(rule: Rule) {
	dialog.confirm({
		title: 'Remove trade discount',
		message: `Remove ${rule.discount_percentage}% off ${rule.item_group} for trade customers? Its Pricing Rule is deleted.`,
		theme: 'red',
		confirmLabel: 'Remove',
		onConfirm: () => removeRule(rule.name),
	})
}

async function removeRule(name: string) {
	removing.value = name
	try {
		updated.value = await call('shop.api.trade.remove_discount', { rule: name })
		toast.success('Trade discount removed')
	} catch (error) {
		const messages = (error as { messages?: string[] }).messages
		toast.error(messages?.[0] || 'Could not remove the discount')
	} finally {
		removing.value = ''
	}
}
</script>
