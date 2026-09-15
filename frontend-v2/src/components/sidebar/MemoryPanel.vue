<script setup lang="ts">
import { ref } from "vue";
import { useMemoryStore } from "@/stores/memory";
import { useDialogStore } from "@/stores/dialog";
import EmptyState from "@/components/common/EmptyState.vue";
import Icon from "@/components/common/Icon.vue";
import Skeleton from "@/components/common/Skeleton.vue";

const memory = useMemoryStore();
const ui = useDialogStore();
const input = ref("");
const search = ref("");
let debounceTimer: number | undefined;

function onSearch() {
  clearTimeout(debounceTimer);
  debounceTimer = window.setTimeout(() => memory.load(search.value), 250);
}

async function add() {
  const c = input.value.trim();
  if (!c) return;
  try {
    await memory.add(c);
    input.value = "";
  } catch (e) {
    await ui.alertError("保存记忆失败", e);
  }
}

async function remove(id: string) {
  try {
    await memory.remove(id);
  } catch (e) {
    await ui.alertError("删除记忆失败", e);
  }
}
</script>

<template>
  <div class="flex min-h-0 flex-1 flex-col">
    <div class="flex-none">
      <div class="relative mb-1.5">
        <Icon
          name="search"
          :size="12"
          class="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-ink-faint"
        />
        <input
          v-model="search"
          type="text"
          class="w-full rounded-lg border border-line-2 bg-surface-2 py-1.5 pl-7 pr-2.5 text-xs text-ink outline-none placeholder:text-ink-faint focus:border-accent coarse:min-h-9"
          placeholder="搜索记忆…"
          @input="onSearch"
        />
      </div>
      <div class="mb-1.5 flex gap-1.5">
        <input
          v-model="input"
          type="text"
          class="min-w-0 flex-1 rounded-lg border border-line-2 bg-surface-2 px-2.5 py-1.5 text-xs text-ink outline-none placeholder:text-ink-faint focus:border-accent coarse:min-h-9"
          placeholder="记住一条信息…"
          @keydown.enter="add"
        />
        <button
          class="grid h-[30px] w-[30px] flex-shrink-0 place-items-center rounded-lg border border-line-2 text-ink-dim transition hover:border-accent/50 hover:text-ink coarse:h-9 coarse:w-9"
          title="添加记忆"
          @click="add"
        >
          <Icon name="plus" :size="13" />
        </button>
      </div>
    </div>
    <div v-if="memory.list.length" class="no-scrollbar min-h-0 flex-1 overflow-y-auto">
      <div class="flex flex-col gap-px">
        <div
          v-for="m in memory.list"
          :key="m.id"
          class="group flex items-center gap-2 rounded-md px-1.5 py-[5px] text-xs text-ink-dim transition hover:bg-surface-2 hover:text-ink"
          :title="m.content"
        >
          <Icon name="brain" :size="13" class="flex-shrink-0 text-ink-faint" />
          <span class="min-w-0 flex-1 truncate">{{ m.content }}</span>
          <button
            class="hidden h-5 w-5 flex-shrink-0 place-items-center rounded text-ink-faint transition hover:bg-err/10 hover:text-err group-hover:grid coarse:h-8 coarse:w-8"
            title="删除记忆"
            @click="remove(m.id)"
          >
            <Icon name="x" :size="12" />
          </button>
        </div>
      </div>
    </div>
    <div v-else-if="memory.loading" class="px-0.5 pt-0.5">
      <Skeleton :rows="3" />
      <span class="sr-only">正在加载记忆…</span>
    </div>
    <EmptyState v-else text="暂无记忆" />
  </div>
</template>
