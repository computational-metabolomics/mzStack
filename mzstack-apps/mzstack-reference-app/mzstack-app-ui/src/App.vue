<script setup lang="ts">
import { computed, onMounted } from 'vue'
import { useRoute } from 'vue-router'

import { api } from '@/api/client'
import type { Health } from '@/api/types'
import logo from '@/assets/mzStack-logo.png'
import { useAsync } from '@/composables/useAsync'

const { data: health, error, run } = useAsync<Health>()
const route = useRoute()

const LINKS = [
  { to: '/datasets', label: 'Datasets' },
  { to: '/create', label: 'Create' },
  { to: '/jobs', label: 'Jobs' },
]

// The landing page lays out its own full-bleed hero and footer; every other
// view sits in the standard container.
const framed = computed(() => route.name !== 'home')

onMounted(() => run(api.health))
</script>

<template>
  <div class="mz-app">
    <header
      class="vf-u-background-color-ui--white"
      style="border-bottom: 1px solid var(--mz-border)"
    >
      <div class="mz-container mz-cluster" style="padding-block: 0.75rem">
        <RouterLink to="/" class="mz-brand mz-cluster mz-cluster--tight">
          <img :src="logo" alt="mzStack" class="mz-brand__logo" />
        </RouterLink>

        <nav class="vf-navigation vf-navigation--main">
          <!-- `mz-cluster` does the spacing: vf's inline list lays the items
               out in a row but leaves no gap between them. -->
          <ul class="mz-cluster" style="list-style: none; margin: 0; padding: 0">
            <li v-for="link in LINKS" :key="link.to" class="vf-navigation__item">
              <!-- vf marks the current item off `aria-current`, which
                   RouterLink sets on an exact match by itself. -->
              <RouterLink :to="link.to" class="vf-navigation__link">
                {{ link.label }}
              </RouterLink>
            </li>
          </ul>
        </nav>

        <span
          v-if="health"
          class="mz-push-right mz-mono vf-text-body--4 vf-u-text-color--grey
            vf-u-text--nowrap"
          :title="health.workspace"
          style="overflow: hidden; text-overflow: ellipsis; max-width: 24rem"
        >
          {{ health.workspace }}
        </span>
      </div>
    </header>

    <!--
      An unreachable API leaves every page unable to work, so this banner
      shows above the view and names the command that starts the process.
    -->
    <div v-if="error" class="mz-container vf-u-padding__top--400">
      <div
        class="vf-banner vf-banner--notice vf-u-background-color--red--light
          vf-u-padding--400"
      >
        <p class="vf-banner__text">
          <strong>Cannot reach the mzStack API.</strong>
          {{ error }} Start it with
          <code class="mz-mono">flask --app mzstack_api run --port 5000</code>
          in <span class="mz-mono">mzstack-app-api</span>.
        </p>
      </div>
    </div>

    <main
      :class="
        framed
          ? 'mz-container vf-u-padding__top--600 vf-u-padding__bottom--800'
          : ''
      "
    >
      <RouterView />
    </main>

    <!--
      `vf-footer` is a dark ground with a green top rule and white text. Its
      `--light` modifier inverts that text for a light ground, and stays off.
    -->
    <footer class="vf-footer">
      <div class="mz-container vf-u-padding__top--600 vf-u-padding__bottom--600">
        <div class="mz-cluster vf-text-body--3">
          <span class="mz-tabular">
            {{ health ? `mzStack ${health.mzstack_version}` : 'mzStack' }}
          </span>
          <span
            v-if="health"
            class="mz-push-right mz-mono vf-u-text--break"
            :title="health.workspace"
          >
            {{ health.workspace }}
          </span>
        </div>
      </div>
    </footer>
  </div>
</template>
