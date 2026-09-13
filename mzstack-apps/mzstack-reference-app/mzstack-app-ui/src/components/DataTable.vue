<script setup lang="ts">
/**
 * A scrollable results table.
 *
 * Spectra tables are wide -- thirty-odd instrument columns -- so the table
 * scrolls inside its own box rather than stretching the page, and numbers are
 * right-aligned and tabular so a column of m/z values can be read down.
 */
import { computed } from 'vue'

import type { Row } from '@/api/types'

const props = withDefaults(
  defineProps<{
    columns: string[]
    rows: Row[]
    /** Column holding the id a click should emit. */
    idColumn?: string
    selectedId?: number | null
    loading?: boolean
    emptyMessage?: string
    /** Drop the surrounding panel, for a caller that is already one. */
    bare?: boolean
  }>(),
  { bare: false },
)

const emit = defineEmits<{ select: [id: number, row: Row] }>()

function isNumeric(value: unknown): value is number {
  return typeof value === 'number'
}

/**
 * How many decimals each numeric column needs.
 *
 * Decided per column rather than per cell: a retention time column showing
 * `30` next to `30.5000` reads as two different quantities. If any value in
 * the column has a fractional part, they all get the same decimals.
 */
const decimals = computed(() => {
  const out: Record<string, number> = {}
  for (const column of props.columns) {
    let fractional = false
    for (const row of props.rows) {
      const value = row[column]
      if (typeof value === 'number' && !Number.isInteger(value)) {
        fractional = true
        break
      }
    }
    out[column] = fractional ? 4 : 0
  }
  return out
})

function format(value: unknown, column: string): string {
  if (value === null || value === undefined) return '—'
  if (typeof value === 'number') {
    if (Math.abs(value) >= 1e7 || (value !== 0 && Math.abs(value) < 1e-3)) {
      return value.toExponential(3)
    }
    return value.toFixed(decimals.value[column] ?? 0)
  }
  if (typeof value === 'boolean') return value ? 'yes' : 'no'
  return String(value)
}

/**
 * Paths are shown by their last component. `dataOrigin` holds an absolute
 * path, usually identical across every row; the full value stays in the
 * tooltip.
 */
function display(value: unknown, column: string): string {
  const text = format(value, column)
  if (typeof value === 'string' && value.includes('/')) {
    return value.split('/').pop() || text
  }
  return text
}

function rowId(row: Row): number | null {
  if (!props.idColumn) return null
  const value = row[props.idColumn]
  return typeof value === 'number' ? value : null
}

function onClick(row: Row) {
  const id = rowId(row)
  if (id !== null) emit('select', id, row)
}
</script>

<template>
  <div :class="bare ? '' : 'mz-panel'">
    <div class="mz-table-scroll">
      <table class="vf-table vf-table--tight vf-table--striped">
        <thead class="vf-table__header">
          <tr class="vf-table__row">
            <th
              v-for="column in columns"
              :key="column"
              class="vf-table__heading vf-u-text--nowrap"
              scope="col"
            >
              {{ column }}
            </th>
          </tr>
        </thead>
        <tbody class="vf-table__body">
          <tr
            v-for="(row, index) in rows"
            :key="index"
            class="vf-table__row"
            :class="[
              idColumn ? 'mz-row--clickable' : '',
              rowId(row) !== null && rowId(row) === selectedId
                ? 'vf-table__row--selected'
                : '',
            ]"
            @click="onClick(row)"
          >
            <td
              v-for="column in columns"
              :key="column"
              class="vf-table__cell vf-u-text--nowrap"
              :class="isNumeric(row[column]) ? 'mz-tabular' : ''"
              :style="
                isNumeric(row[column])
                  ? { textAlign: 'right', maxWidth: '16rem' }
                  : { maxWidth: '16rem', overflow: 'hidden', textOverflow: 'ellipsis' }
              "
              :title="row[column] === null ? '' : String(row[column])"
            >
              {{ display(row[column], column) }}
            </td>
          </tr>
          <tr v-if="!rows.length && !loading" class="vf-table__row">
            <td
              :colspan="Math.max(columns.length, 1)"
              class="vf-table__cell vf-u-text-color--grey"
              style="padding: 2rem; text-align: center"
            >
              {{ emptyMessage ?? 'Nothing to show.' }}
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
