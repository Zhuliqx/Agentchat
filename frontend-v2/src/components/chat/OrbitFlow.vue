<script setup lang="ts">
// 编排轨道：Agent 调度可视化（Supervisor → 各 Agent → 完成）
// 签名元素：青色脉冲节点，代表「路由 / 信号」
import type { OrbitNode } from "@/stores/chat";
import Icon from "@/components/common/Icon.vue";

const props = defineProps<{ nodes: OrbitNode[]; streaming?: boolean }>();

const nodeCls = (type: OrbitNode["type"]) =>
  type === "start"
    ? "text-accent"
    : type === "end"
      ? "text-ok"
      : type === "error"
        ? "text-err"
        : "text-orbit";

// 仅「工具调用执行中」的节点才闪烁（active 由 store 控制：tool 事件激活、答案 token 停止）
const isPulsing = (n: OrbitNode) => !!n.active;
</script>

<template>
  <div v-if="nodes?.length" class="mt-2 flex flex-wrap items-center gap-y-1.5">
    <template v-for="(n, i) in nodes" :key="i">
      <!-- 连接线 -->
      <span v-if="i > 0" class="mx-1.5 h-px w-3.5 bg-line-2" />

      <span
        class="inline-flex items-center gap-1.5 text-[11px]"
        :class="[nodeCls(n.type), n.type === 'tool' ? 'font-medium' : '']"
      >
        <!-- 节点图标 -->
        <span class="relative grid h-[14px] w-[14px] place-items-center">
          <span
            v-if="isPulsing(n)"
            class="absolute -inset-0.5 animate-ping rounded-full bg-orbit/40"
          />
          <Icon
            v-if="n.type === 'start'"
            name="orbit"
            :size="13"
            class="relative"
          />
          <Icon
            v-else-if="n.type === 'end'"
            name="check"
            :size="12"
            class="relative"
          />
          <Icon
            v-else-if="n.type === 'error'"
            name="x"
            :size="12"
            class="relative"
          />
          <span
            v-else
            class="relative h-1.5 w-1.5 rounded-full bg-orbit"
          />
        </span>
        <span class="tracking-tight">{{ n.label }}</span>
      </span>
    </template>
  </div>
</template>
