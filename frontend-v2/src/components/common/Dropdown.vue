<script setup lang="ts">
// 通用下拉菜单：相对锚点绝对定位（不 Teleport，避免 fixed 定位偏移问题）
import { ref } from "vue";
import { onClickOutside } from "@vueuse/core";

const props = defineProps<{
  open: boolean;
  align?: "right" | "left";
  /** 展开方向：up（触发器上方，默认）| down（触发器下方） */
  placement?: "up" | "down";
}>();
const emit = defineEmits<{ close: [] }>();
const root = ref<HTMLElement | null>(null);
onClickOutside(root, () => {
  if (props.open) emit("close");
});
</script>

<template>
  <div ref="root" class="relative inline-flex">
    <slot name="trigger" />
    <Transition name="pop">
      <div
        v-if="open"
        class="absolute z-[60] min-w-[200px] rounded-xl border border-line-2 bg-surface p-1 shadow-[0_16px_48px_rgba(0,0,0,0.5)]"
        :class="[
          placement === 'down' ? 'top-[calc(100%+8px)]' : 'bottom-[calc(100%+8px)]',
          align === 'left' ? 'left-0' : 'right-0',
        ]"
      >
        <slot />
      </div>
    </Transition>
  </div>
</template>
