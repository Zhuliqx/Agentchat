<script setup lang="ts">
defineProps<{ title: string; open: boolean; small?: boolean }>();
const emit = defineEmits<{ close: [] }>();
</script>

<template>
  <Teleport to="body">
    <Transition name="fade">
      <div
        v-if="open"
        class="fixed inset-0 z-50 grid place-items-center bg-black/60 p-4 backdrop-blur-[2px]"
        @click.self="emit('close')"
      >
        <Transition name="pop" appear>
          <div
            class="flex max-h-[86vh] w-full flex-col overflow-hidden rounded-xl border border-line-2 bg-surface shadow-[0_24px_64px_rgba(0,0,0,0.5)]"
            :class="small ? 'max-w-[400px]' : 'max-w-[600px]'"
          >
            <div class="flex flex-shrink-0 items-center justify-between border-b border-line px-5 py-3.5">
              <span class="text-[14px] font-medium tracking-tight">{{ title }}</span>
              <button
                class="grid h-7 w-7 place-items-center rounded-md text-ink-faint transition hover:bg-surface-2 hover:text-ink"
                aria-label="关闭"
                @click="emit('close')"
              >
                <svg viewBox="0 0 24 24" class="h-4 w-4" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round">
                  <path d="M18 6L6 18M6 6l12 12" />
                </svg>
              </button>
            </div>
            <div class="min-h-0 flex-1 overflow-y-auto px-5 py-4">
              <slot />
            </div>
          </div>
        </Transition>
      </div>
    </Transition>
  </Teleport>
</template>
