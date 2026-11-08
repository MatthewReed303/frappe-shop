<template>
	<div class="space-y-3">
		<div>
			<div class="text-sm font-medium text-ink-gray-8">Trade discounts</div>
			<p class="text-p-sm text-ink-gray-5">
				Percent off an Item Group for the trade group, kept as ERPNext Pricing Rules. Rules by item or quantity,
				and customer prices, are set up in Desk.
			</p>
		</div>
		<div
			v-for="rule in rules"
			:key="rule.name"
			class="flex max-w-lg items-center justify-between rounded border border-outline-gray-1 px-3 py-2 text-base"
		>
			<span class="text-ink-gray-8">{{ rule.item_group }}</span>
			<span class="flex items-center gap-3">
				<span class="text-ink-gray-7">{{ rule.discount_percentage }}% off</span>
				<Button variant="ghost" theme="red" @click="remove(rule.name)">Remove</Button>
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
import { Button, FormControl, call, createResource, toast } from 'frappe-ui'

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

async function remove(rule: string) {
	updated.value = await call('shop.api.trade.remove_discount', { rule })
	toast.success('Trade discount removed')
}
</script>
