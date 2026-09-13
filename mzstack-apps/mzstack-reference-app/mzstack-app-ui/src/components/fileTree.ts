/**
 * What a tree node needs from the tree.
 *
 * A node is rendered by a node, to any depth. The tree provides this once and
 * every node injects it.
 */

import type { InjectionKey, Ref } from 'vue'

import type { FileEntry, FileKind } from '@/api/types'

export interface FileTreeContext {
  /**
   * Which entries may be selected; directories are navigable regardless.
   * A ref, not a value: a caller may switch what it is asking for, and every
   * node's idea of what is selectable has to switch with it.
   */
  accept: Ref<FileKind>
  multiple: Ref<boolean>
  /** Selected absolute paths, shared with the caller's `v-model`. */
  selected: Ref<string[]>
  /** A directory's entries, once it has been opened. Absent means unread. */
  children: Ref<Map<string, FileEntry[]>>
  /** Directories currently showing their children. */
  expanded: Ref<Set<string>>
  /** Directories with a request in flight, and those whose request failed. */
  loading: Ref<Set<string>>
  failed: Ref<Map<string, string>>
  toggleExpanded: (path: string) => void
  toggleSelected: (entry: FileEntry) => void
  /** Select, or clear, every selectable entry under an opened directory. */
  setSubtree: (path: string, select: boolean) => void
}

export const FILE_TREE: InjectionKey<FileTreeContext> = Symbol('file-tree')

/**
 * Whether listing `entry` can succeed: real directories always, and an
 * mzStack dataset unless the dataset itself is what is being picked. A
 * `.mzpeak` archive is a file, so it is a leaf.
 */
export function expandable(entry: FileEntry, accept: FileKind): boolean {
  if (entry.kind === 'directory') return true
  return entry.kind === 'mzstack' && accept !== 'mzstack'
}

export function humanSize(bytes: number | null): string {
  if (bytes === null) return ''
  const units = ['B', 'kB', 'MB', 'GB', 'TB']
  let value = bytes
  let unit = 0
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024
    unit++
  }
  return `${value.toFixed(unit === 0 ? 0 : 1)} ${units[unit]}`
}
