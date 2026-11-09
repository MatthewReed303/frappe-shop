<template>
	<div class="mx-auto max-w-5xl px-6 py-8">
		<UiPageHeader
			title="Item Groups"
			subtitle="How products are grouped in ERPNext, for reports, accounts and rates (percent off an Item Group). A group's rates also cover the groups inside it."
		>
			<template #actions>
				<Button variant="solid" @click="openEditor()">
					<template #prefix><LucidePlus class="size-4" /></template>
					New Item Group
				</Button>
			</template>
		</UiPageHeader>

		<div v-if="tree.error" class="mt-6 flex flex-col items-center gap-3 py-16 text-center">
			<p class="text-p-base text-ink-red-8">{{ errorMessage(tree.error, 'Could not load the Item Groups') }}</p>
			<Button @click="tree.reload()">Try again</Button>
		</div>

		<UiDataTable v-else class="mt-6" :columns="columns" :rows="groups" row-key="name" :loading="!tree.fetched">
			<template #cell-name="{ row }">
				<span :style="{ paddingLeft: `${(row.depth - 1) * 1.25}rem` }" class="font-medium text-ink-gray-8">
					{{ row.name }}
				</span>
			</template>
			<template #cell-actions="{ row }">
				<div v-if="row.parent" class="flex justify-end gap-1">
					<Button variant="ghost" @click="openEditor(row as ItemGroup)">Edit</Button>
					<Button
						variant="ghost"
						theme="red"
						:disabled="row.children || row.products > 0"
						:title="row.children || row.products ? 'Move what is in it first' : ''"
						@click="confirmDelete(row as ItemGroup)"
					>
						Remove
					</Button>
				</div>
			</template>
		</UiDataTable>

		<Dialog
			v-model="editing"
			:options="{
				title: form.name ? `Edit ${form.name}` : 'New Item Group',
				actions: [{ label: 'Save', variant: 'solid', loading: saving, onClick: save }],
			}"
		>
			<div class="space-y-4">
				<FormControl v-model="form.item_group_name" label="Name" required />
				<FormControl v-model="form.parent" type="select" label="Inside" :options="parentOptions" />
				<p class="text-p-sm text-ink-gray-5">
					Products are put in an Item Group on their own page. Renaming keeps the products, rates and records
					that use it.
				</p>
			</div>
		</Dialog>
	</div>
</template>

<script setup lang="ts">
import { computed, reactive, ref } from 'vue'
import { Button, Dialog, FormControl, call, createResource, dialog, toast } from 'frappe-ui'

import LucidePlus from '~icons/lucide/plus'

import UiDataTable from '@/components/UiDataTable.vue'
import UiPageHeader from '@/components/UiPageHeader.vue'

interface ItemGroup {
	name: string
	parent: string | null
	depth: number
	products: number
	children: boolean
}

const tree = createResource({ url: 'shop.api.products.get_item_group_tree', auto: true })
const groups = computed<ItemGroup[]>(() => tree.data || [])

const columns = [
	{ key: 'name', label: 'Item Group' },
	{ key: 'products', label: 'Products', align: 'right' as const },
	{ key: 'actions', label: '', align: 'right' as const },
]

function errorMessage(error: unknown, fallback: string) {
	const messages = (error as { messages?: string[] } | null)?.messages
	return messages?.[0] || fallback
}

const editing = ref(false)
const saving = ref(false)
const form = reactive({ name: '', item_group_name: '', parent: '' })

// a group can't go inside itself or one of its own groups
const parentOptions = computed(() => {
	const all = groups.value
	const start = all.findIndex((group) => group.name === form.name)
	const inside = new Set<string>()
	if (start >= 0) {
		inside.add(form.name)
		for (const group of all.slice(start + 1)) {
			if (group.depth <= all[start].depth) break
			inside.add(group.name)
		}
	}
	return all
		.filter((group) => !inside.has(group.name))
		.map((group) => ({ label: `${'  '.repeat(group.depth - 1)}${group.name}`, value: group.name }))
})

function openEditor(group?: ItemGroup) {
	Object.assign(form, {
		name: group?.name || '',
		item_group_name: group?.name || '',
		parent: group?.parent || groups.value[0]?.name || '',
	})
	editing.value = true
}

async function save() {
	saving.value = true
	try {
		tree.data = await call('shop.api.products.save_item_group', {
			name: form.name || undefined,
			item_group_name: form.item_group_name,
			parent: form.parent,
		})
		editing.value = false
		toast.success('Item Group saved')
	} catch (error) {
		toast.error(errorMessage(error, 'Could not save the Item Group'))
	} finally {
		saving.value = false
	}
}

function confirmDelete(group: ItemGroup) {
	dialog.confirm({
		title: 'Remove Item Group',
		message: `Remove <b>${group.name}</b>?`,
		theme: 'red',
		confirmLabel: 'Remove',
		onConfirm: async () => {
			try {
				tree.data = await call('shop.api.products.delete_item_group', { name: group.name })
				toast.success('Item Group removed')
			} catch (error) {
				toast.error(errorMessage(error, 'Could not remove the Item Group'))
			}
		},
	})
}
</script>
