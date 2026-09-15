<script setup lang="ts">
import { nextTick, onBeforeUnmount, ref, useId, watch } from "vue";

const props = defineProps<{ title: string; open: boolean; small?: boolean }>();
const emit = defineEmits<{ close: [] }>();
const panel = ref<HTMLElement | null>(null);
const titleId = useId();
let previousFocus: HTMLElement | null = null;
let previousOverflow = "";

function focusableItems(): HTMLElement[] {
  const root = panel.value;
  if (!root) return [];
  return Array.from(
    root.querySelectorAll<HTMLElement>(
      'a[href], button:not([disabled]), textarea, input, select, [tabindex]:not([tabindex="-1"])',
    ),
  );
}

function restorePageState() {
  document.body.style.overflow = previousOverflow;
  if (previousFocus?.isConnected) previousFocus.focus();
  previousFocus = null;
}

function onKeydown(e: KeyboardEvent) {
  if (e.key === "Escape") {
    e.stopPropagation();
    emit("close");
    return;
  }
  if (e.key !== "Tab") return;
  const items = focusableItems();
  if (!items.length) {
    e.preventDefault();
    panel.value?.focus();
    return;
  }
  const first = items[0];
  const last = items[items.length - 1];
  if (e.shiftKey && document.activeElement === first) {
    e.preventDefault();
    last.focus();
  } else if (!e.shiftKey && document.activeElement === last) {
    e.preventDefault();
    first.focus();
  }
}

watch(
  () => props.open,
  async (open) => {
    if (open) {
      previousFocus = document.activeElement as HTMLElement | null;
      previousOverflow = document.body.style.overflow;
      document.body.style.overflow = "hidden";
      await nextTick();
      (focusableItems()[0] ?? panel.value)?.focus();
    } else {
      restorePageState();
    }
  },
  { immediate: true },
);

onBeforeUnmount(restorePageState);
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
            ref="panel"
            role="dialog"
            aria-modal="true"
            :aria-labelledby="titleId"
            tabindex="-1"
            class="flex max-h-[86vh] w-full flex-col overflow-hidden rounded-xl border border-line-2 bg-surface shadow-[0_24px_64px_rgba(0,0,0,0.5)] outline-none"
            :class="small ? 'max-w-[400px]' : 'max-w-[600px]'"
            @keydown="onKeydown"
          >
            <div
              class="flex flex-shrink-0 items-center justify-between border-b border-line px-5 py-3.5"
            >
              <span :id="titleId" class="text-base font-medium tracking-tight">{{ title }}</span>
              <button
                class="grid h-7 w-7 place-items-center rounded-md text-ink-faint transition hover:bg-surface-2 hover:text-ink coarse:h-9 coarse:w-9"
                aria-label="关闭"
                @click="emit('close')"
              >
                <svg
                  viewBox="0 0 24 24"
                  class="h-4 w-4"
                  fill="none"
                  stroke="currentColor"
                  stroke-width="1.8"
                  stroke-linecap="round"
                >
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
