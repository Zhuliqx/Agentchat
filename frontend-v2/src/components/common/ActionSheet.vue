<script setup lang="ts">
import { nextTick, onBeforeUnmount, ref, useId, watch } from "vue";
import Icon from "@/components/common/Icon.vue";

export interface ActionSheetItem {
  key: string;
  label: string;
  icon?: string;
  /** 破坏性操作（删除等）用错误色区分，避免误点 */
  danger?: boolean;
}

const props = defineProps<{
  open: boolean;
  title?: string;
  items: ActionSheetItem[];
}>();
const emit = defineEmits<{ select: [key: string]; close: [] }>();

const panel = ref<HTMLElement | null>(null);
const titleId = useId();

function onKeydown(e: KeyboardEvent) {
  if (e.key === "Escape") {
    e.stopPropagation();
    emit("close");
  }
}

watch(
  () => props.open,
  async (open) => {
    if (!open) {
      document.removeEventListener("keydown", onKeydown, true);
      return;
    }
    document.addEventListener("keydown", onKeydown, true);
    await nextTick();
    // 长按结束后焦点不会自己进来：这里落焦，键盘与读屏才有上下文
    panel.value?.querySelector<HTMLElement>("button")?.focus();
  },
  { immediate: true },
);

onBeforeUnmount(() => document.removeEventListener("keydown", onKeydown, true));
</script>

<template>
  <Teleport to="body">
    <Transition name="fade">
      <div
        v-if="open"
        class="fixed inset-0 z-50 flex items-end justify-center bg-black/40 backdrop-blur-[1px] sm:items-center"
        @pointerdown.self="emit('close')"
      >
        <Transition name="pop" appear>
          <div
            ref="panel"
            role="menu"
            :aria-labelledby="title ? titleId : undefined"
            class="mb-2 w-[calc(100%-1rem)] max-w-[380px] rounded-2xl border border-line-2 bg-surface p-1.5 shadow-[0_24px_64px_rgba(0,0,0,0.5)]"
          >
            <div v-if="title" :id="titleId" class="truncate px-3 pb-1 pt-2 text-2xs text-ink-faint">
              {{ title }}
            </div>
            <button
              v-for="it in items"
              :key="it.key"
              role="menuitem"
              class="flex min-h-10 w-full items-center gap-2.5 rounded-xl px-3 py-2.5 text-left text-sm transition hover:bg-surface-2"
              :class="it.danger ? 'text-err hover:bg-err/10' : 'text-ink-dim hover:text-ink'"
              @click="emit('select', it.key)"
            >
              <Icon v-if="it.icon" :name="it.icon" :size="15" class="flex-shrink-0" />
              <span class="min-w-0 flex-1 truncate">{{ it.label }}</span>
            </button>
            <button
              class="mt-1 min-h-10 w-full rounded-xl border border-line-2 px-3 py-2.5 text-sm text-ink-dim transition hover:bg-surface-2 hover:text-ink"
              @click="emit('close')"
            >
              取消
            </button>
          </div>
        </Transition>
      </div>
    </Transition>
  </Teleport>
</template>
