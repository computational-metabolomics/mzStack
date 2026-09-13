<script setup lang="ts">
/**
 * Picking files on the server.
 *
 * Nothing is uploaded: the user checks what is already there, in the
 * directories the API is configured to expose. mzML files are routinely
 * gigabytes and mzPeak archives are read in place.
 *
 * A selection can span several directories at once. The API lists one
 * directory per call, so a branch is read when it is first opened and kept
 * after that.
 */
import { onMounted, provide, ref, toRef, watch } from 'vue'

import { api, ApiError } from '@/api/client'
import type { FileEntry, FileKind } from '@/api/types'

import FileTreeNode from './FileTreeNode.vue'
import { expandable, FILE_TREE } from './fileTree'

const props = withDefaults(
  defineProps<{
    /** Which entries may be selected; directories are always navigable. */
    accept: FileKind
    multiple?: boolean
  }>(),
  { multiple: false },
)

const selected = defineModel<string[]>({ default: () => [] })

const roots = ref<FileEntry[]>([])
const children = ref(new Map<string, FileEntry[]>())
const expanded = ref(new Set<string>())
const loading = ref(new Set<string>())
const failed = ref(new Map<string, string>())
const bootError = ref<string | null>(null)
const booting = ref(true)

/** The last segment of a path, for a root shown by name rather than in full. */
function basename(path: string): string {
  const parts = path.split('/').filter(Boolean)
  return parts[parts.length - 1] ?? path
}

/**
 * Reads a directory once and remembers it.
 *
 * Re-reading on every expand would make collapsing a branch a way to
 * invalidate it, which is not what a twisty means. A failure is remembered
 * too, so a permission error is shown where it happened rather than
 * retried on every toggle.
 */
async function read(path: string) {
  if (children.value.has(path) || loading.value.has(path)) return
  loading.value = new Set(loading.value).add(path)
  failed.value.delete(path)
  try {
    const listing = await api.files(path)
    children.value = new Map(children.value).set(path, listing.entries)
  } catch (exc) {
    const message =
      exc instanceof ApiError ? exc.message : (exc as Error).message
    failed.value = new Map(failed.value).set(path, message)
  } finally {
    const next = new Set(loading.value)
    next.delete(path)
    loading.value = next
  }
}

function toggleExpanded(path: string) {
  const next = new Set(expanded.value)
  if (next.has(path)) next.delete(path)
  else {
    next.add(path)
    void read(path)
  }
  expanded.value = next
}

function toggleSelected(entry: FileEntry) {
  if (entry.kind !== props.accept) return
  const current = selected.value
  if (current.includes(entry.path)) {
    selected.value = current.filter((p) => p !== entry.path)
  } else {
    selected.value = props.multiple ? [...current, entry.path] : [entry.path]
  }
}

/** Every selectable path under `path`, across the branches already read. */
function selectableUnder(path: string): string[] {
  const entries = children.value.get(path)
  if (!entries) return []
  const found: string[] = []
  for (const child of entries) {
    if (child.kind === props.accept) found.push(child.path)
    if (expandable(child, props.accept)) found.push(...selectableUnder(child.path))
  }
  return found
}

function setSubtree(path: string, select: boolean) {
  const under = selectableUnder(path)
  if (!under.length) return
  if (!select) {
    selected.value = selected.value.filter((p) => !under.includes(p))
    return
  }
  selected.value = props.multiple
    ? [...new Set([...selected.value, ...under])]
    : [under[0]]
}

provide(FILE_TREE, {
  accept: toRef(props, 'accept'),
  multiple: toRef(props, 'multiple'),
  selected,
  children,
  expanded,
  loading,
  failed,
  toggleExpanded,
  toggleSelected,
  setSubtree,
})

/**
 * The roots, as the top level.
 *
 * A listing carries every configured root, and `path=null` returns the first
 * of them, so one call both names the roots and fills in the one the user is
 * most likely to want open.
 */
async function boot() {
  booting.value = true
  bootError.value = null
  children.value = new Map()
  expanded.value = new Set()
  failed.value = new Map()
  try {
    const listing = await api.files(null)
    roots.value = listing.roots.map((path) => ({
      name: basename(path),
      path,
      kind: 'directory' as const,
      size: null,
    }))
    children.value = new Map([[listing.path, listing.entries]])
    expanded.value = new Set([listing.path])
  } catch (exc) {
    bootError.value =
      exc instanceof ApiError ? exc.message : (exc as Error).message
  } finally {
    booting.value = false
  }
}

onMounted(boot)
// `accept` decides what is selectable and what is worth opening, so a change
// invalidates both the selection and the branches read under the old rule.
watch(
  () => props.accept,
  () => {
    selected.value = []
    void boot()
  },
)
</script>

<template>
  <div class="mz-panel">
    <p
      v-if="bootError"
      class="vf-text-body--3 vf-u-text-color--red vf-u-padding--400"
    >
      {{ bootError }}
    </p>

    <ul v-else class="mz-browser">
      <li v-if="booting" class="mz-browser__empty">Loading…</li>
      <li v-else-if="!roots.length" class="mz-browser__empty">
        No browsable directory is configured.
      </li>
      <template v-else>
        <FileTreeNode
          v-for="root in roots"
          :key="root.path"
          :entry="root"
          :depth="0"
        />
      </template>
    </ul>

    <div
      v-if="selected.length"
      class="mz-cluster mz-cluster--tight vf-text-body--4
        vf-u-background-color-ui--grey--light vf-u-padding--200"
      style="border-top: 1px solid var(--mz-border)"
    >
      {{ selected.length }} selected
      <button class="vf-button vf-button--link" @click="selected = []">
        clear
      </button>
    </div>
  </div>
</template>
