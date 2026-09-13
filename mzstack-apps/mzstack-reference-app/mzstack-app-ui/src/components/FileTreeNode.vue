<script setup lang="ts">
/**
 * One row of the file tree, and its children.
 *
 * The component renders itself, so a directory's contents are the same rows
 * one level deeper. Everything it needs comes from the tree's injected
 * context.
 */
import { computed, inject } from 'vue'

import type { FileEntry } from '@/api/types'

import { expandable, FILE_TREE, humanSize } from './fileTree'

const props = defineProps<{ entry: FileEntry; depth: number }>()

const tree = inject(FILE_TREE)!

const isOpen = computed(() => tree.expanded.value.has(props.entry.path))
const isBusy = computed(() => tree.loading.value.has(props.entry.path))
const failure = computed(() => tree.failed.value.get(props.entry.path))
const children = computed(() => tree.children.value.get(props.entry.path))

const canExpand = computed(() => expandable(props.entry, tree.accept.value))
const canSelect = computed(() => props.entry.kind === tree.accept.value)
const isSelected = computed(() => tree.selected.value.includes(props.entry.path))

/**
 * Every selectable path under this directory that has been read.
 *
 * A collapsed subtree has not been listed, so it contributes nothing and the
 * box reads as empty.
 */
function selectableUnder(path: string): string[] {
  const entries = tree.children.value.get(path)
  if (!entries) return []
  const accept = tree.accept.value
  const found: string[] = []
  for (const child of entries) {
    if (child.kind === accept) found.push(child.path)
    if (expandable(child, accept)) found.push(...selectableUnder(child.path))
  }
  return found
}

/** A directory's own box: checked, indeterminate, or empty. */
const subtree = computed(() => {
  if (!canExpand.value || !tree.multiple.value) return null
  const under = selectableUnder(props.entry.path)
  if (!under.length) return null
  const chosen = under.filter((p) => tree.selected.value.includes(p)).length
  return { all: chosen === under.length, some: chosen > 0 }
})

function activate() {
  if (canExpand.value) tree.toggleExpanded(props.entry.path)
  else if (canSelect.value) tree.toggleSelected(props.entry)
}
</script>

<template>
  <li>
    <div
      class="mz-browser__row mz-cluster mz-cluster--tight vf-text-body--3"
      :class="[
        canExpand || canSelect ? 'mz-row--clickable' : 'vf-u-text-color--grey',
        isSelected ? 'mz-browser__row--selected' : '',
      ]"
      :style="{ '--mz-depth': depth }"
      @click="activate"
    >
      <!-- The twisty is its own button so a directory can be opened from the
           keyboard without the row's click also running. -->
      <button
        v-if="canExpand"
        type="button"
        class="mz-browser__twisty"
        :aria-expanded="isOpen"
        :aria-label="`${isOpen ? 'Collapse' : 'Expand'} ${entry.name}`"
        @click.stop="tree.toggleExpanded(entry.path)"
      >
        {{ isOpen ? '▾' : '▸' }}
      </button>
      <span v-else class="mz-browser__twisty" aria-hidden="true"></span>

      <input
        v-if="canSelect"
        type="checkbox"
        class="mz-browser__check"
        :checked="isSelected"
        :aria-label="entry.name"
        @click.stop="tree.toggleSelected(entry)"
      />
      <input
        v-else-if="subtree"
        type="checkbox"
        class="mz-browser__check"
        :checked="subtree.all"
        :indeterminate="subtree.some && !subtree.all"
        :aria-label="`Everything under ${entry.name}`"
        @click.stop="tree.setSubtree(entry.path, !subtree.all)"
      />
      <span v-else class="mz-browser__spacer" aria-hidden="true"></span>

      <span
        class="vf-u-text--nowrap"
        style="overflow: hidden; text-overflow: ellipsis"
      >
        {{ entry.name }}
      </span>
      <span
        v-if="entry.kind !== 'directory' && entry.kind !== 'other'"
        class="vf-badge"
      >
        {{ entry.kind }}
      </span>
      <span
        class="mz-push-right mz-tabular vf-text-body--4 vf-u-text-color--grey"
      >
        {{ isBusy ? 'opening…' : humanSize(entry.size) }}
      </span>
    </div>

    <ul v-if="isOpen" class="mz-browser__children">
      <li
        v-if="failure"
        class="mz-browser__note vf-u-text-color--red"
        :style="{ '--mz-depth': depth + 1 }"
      >
        {{ failure }}
      </li>
      <li
        v-else-if="!children"
        class="mz-browser__note"
        :style="{ '--mz-depth': depth + 1 }"
      >
        Loading…
      </li>
      <li
        v-else-if="!children.length"
        class="mz-browser__note"
        :style="{ '--mz-depth': depth + 1 }"
      >
        Empty.
      </li>
      <template v-else>
        <FileTreeNode
          v-for="child in children"
          :key="child.path"
          :entry="child"
          :depth="depth + 1"
        />
      </template>
    </ul>
  </li>
</template>
