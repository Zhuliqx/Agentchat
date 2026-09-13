<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, useId } from "vue";

// 触发器 + 传送门是多根结构，Vue 不会自动透传属性，这里手动绑到触发器上，
// 否则调用方写的 class（边距、对齐）会被静默丢弃。
defineOptions({ inheritAttrs: false });

const props = withDefaults(
  defineProps<{ label: string; placement?: "top" | "bottom"; delay?: number }>(),
  { placement: "top", delay: 300 },
);

const GAP = 8; // 提示与触发元素的距离
const EDGE = 4; // 与视口边缘的最小留白

const trigger = ref<HTMLElement | null>(null);
const tip = ref<HTMLElement | null>(null);
const visible = ref(false);
const anchor = ref({ x: 0, y: 0 });
const side = ref<"top" | "bottom">(props.placement);
const tooltipId = useId();
let timer: ReturnType<typeof setTimeout> | null = null;

// 提示浮层挂在 body 上并按触发器位置定位：侧边栏/弹窗都有 overflow-hidden，
// 就地绝对定位会被裁掉。
const style = computed(() => ({
  left: `${anchor.value.x}px`,
  top: `${anchor.value.y}px`,
  transform: side.value === "bottom" ? "translate(-50%, 0)" : "translate(-50%, -100%)",
}));

/** 定位：默认方向放不下就翻到另一侧，水平贴边时向内收，避免提示跑出视口 */
function place() {
  const el = trigger.value;
  const tipEl = tip.value;
  if (!el || !tipEl) return;
  const rect = el.getBoundingClientRect();
  const tipRect = tipEl.getBoundingClientRect();
  let next = props.placement;
  if (next === "top" && rect.top - GAP - tipRect.height < EDGE) next = "bottom";
  else if (next === "bottom" && rect.bottom + GAP + tipRect.height > window.innerHeight - EDGE)
    next = "top";
  side.value = next;
  const half = tipRect.width / 2;
  anchor.value = {
    x: Math.min(Math.max(rect.left + rect.width / 2, half + EDGE), window.innerWidth - half - EDGE),
    y: next === "bottom" ? rect.bottom + GAP : rect.top - GAP,
  };
}

function clearTimer() {
  if (timer) {
    clearTimeout(timer);
    timer = null;
  }
}

async function show() {
  clearTimer();
  timer = setTimeout(async () => {
    if (!trigger.value) return;
    visible.value = true;
    await nextTick();
    // 先渲染再测量，拿到提示真实尺寸后才能判断是否需要翻面/收边
    place();
    window.addEventListener("scroll", hide, true);
    window.addEventListener("resize", hide);
    window.addEventListener("keydown", onEsc);
  }, props.delay);
}

function onEsc(e: KeyboardEvent) {
  if (e.key === "Escape") hide();
}

function hide() {
  clearTimer();
  visible.value = false;
  window.removeEventListener("scroll", hide, true);
  window.removeEventListener("resize", hide);
  window.removeEventListener("keydown", onEsc);
}

onBeforeUnmount(hide);
</script>

<template>
  <span
    ref="trigger"
    class="inline-flex"
    v-bind="$attrs"
    :aria-describedby="visible ? tooltipId : undefined"
    @mouseenter="show"
    @mouseleave="hide"
    @focusin="show"
    @focusout="hide"
    @keydown.escape="hide"
  >
    <slot />
  </span>
  <Teleport to="body">
    <Transition name="fade">
      <span
        v-if="visible"
        ref="tip"
        :id="tooltipId"
        role="tooltip"
        class="pointer-events-none fixed z-[60] whitespace-nowrap rounded-md border border-line-2 bg-surface-3 px-2 py-1 text-2xs text-ink shadow-[0_8px_24px_rgba(0,0,0,0.35)]"
        :style="style"
      >
        {{ label }}
      </span>
    </Transition>
  </Teleport>
</template>
