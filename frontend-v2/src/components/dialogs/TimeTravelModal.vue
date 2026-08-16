<script setup lang="ts">
import { ref, watch } from "vue";
import Modal from "@/components/common/Modal.vue";
import Icon from "@/components/common/Icon.vue";
import EmptyState from "@/components/common/EmptyState.vue";
import { sessionsApi } from "@/api";
import { useSessionsStore } from "@/stores/sessions";
import { useChatStore } from "@/stores/chat";
import type { Checkpoint } from "@/types/api";

const open = defineModel<boolean>({ default: false });
const sessions = useSessionsStore();
const chat = useChatStore();
const list = ref<Checkpoint[]>([]);
const loading = ref(false);
const error = ref("");

const NODE_META: Record<string, [string, string]> = {
  rag_agent: ["doc", "知识库"],
  mcp_agent: ["db", "数据库/工具"],
  web_search: ["globe", "联网搜索"],
  search_agent: ["globe", "联网搜索"], // 兼容旧事件（改名前的历史记录）
  recall_memory: ["brain", "记忆"],
  remember_memory: ["brain", "记忆"],
  request_confirmation: ["warn", "人工确认"],
};
function nodeLabel(name: string) {
  const meta = NODE_META[name];
  if (meta) return `${meta[1]}`;
  return { model: "思考", tools: "工具", __start__: "开始" }[name] || name;
}

watch(open, async (v) => {
  if (!v || !sessions.currentId) return;
  loading.value = true;
  error.value = "";
  list.value = [];
  try {
    list.value = await sessionsApi.checkpoints(sessions.currentId);
  } catch (e) {
    error.value = (e as Error).message;
  } finally {
    loading.value = false;
  }
});

function forkAt(cid: string) {
  const box = (document.getElementById(`fork-${cid}`) as HTMLInputElement)?.value;
  const text = box?.trim();
  if (!text) return;
  open.value = false;
  chat.send(text, { checkpointId: cid });
}
</script>

<template>
  <Modal :open="open" title="版本历史" @close="open = false">
    <div v-if="loading" class="py-6 text-center text-xs text-ink-dim">加载中…</div>
    <div v-else-if="error" class="text-sm text-err">{{ error }}</div>
    <EmptyState v-else-if="!list.length" text="该会话暂无版本历史" icon="clock" />
    <div v-else class="flex flex-col">
      <div v-for="(c, i) in list" :key="c.checkpoint_id" class="relative flex gap-3 pb-1">
        <!-- 时间线 -->
        <div class="flex w-5 flex-shrink-0 flex-col items-center">
          <span class="mt-2 grid h-5 w-5 place-items-center rounded-full border border-line-2 bg-surface-2 text-[10px] font-medium text-ink-dim">
            {{ list.length - i }}
          </span>
          <span v-if="i < list.length - 1" class="w-px flex-1 bg-line" />
        </div>

        <!-- 节点内容 -->
        <div class="min-w-0 flex-1 pb-4">
          <div class="flex flex-wrap items-center gap-1.5 text-[11px]">
            <span class="text-ink-faint">{{ c.created_at ? new Date(c.created_at).toLocaleTimeString() : "" }}</span>
            <span v-if="c.interrupted" class="flex items-center gap-1 rounded-full bg-warn/10 px-2 py-0.5 text-[10.5px] text-warn">
              <Icon name="warn" :size="11" />
              待确认
            </span>
            <span v-else-if="c.next && c.next.length" class="flex items-center gap-1 rounded-full bg-orbit/10 px-2 py-0.5 text-[10.5px] text-orbit">
              <Icon name="zap" :size="10" />
              下一步：{{ c.next.map(nodeLabel).join(", ") }}
            </span>
            <span v-else class="flex items-center gap-1 rounded-full bg-ok/10 px-2 py-0.5 text-[10.5px] text-ok">
              <Icon name="check" :size="10" />
              完成
            </span>
          </div>
          <div class="mt-1.5 truncate text-[12px] text-ink-dim">{{ (c.summary || "(无文本步骤)").slice(0, 72) }}</div>
          <div class="mt-2 flex items-center gap-2">
            <input
              :id="`fork-${c.checkpoint_id}`"
              class="min-w-0 flex-1 rounded-lg border border-line-2 bg-surface-2 px-2.5 py-1.5 text-[12px] text-ink outline-none placeholder:text-ink-faint focus:border-accent"
              placeholder="输入要从该步重新生成的消息…"
              @keydown.enter="forkAt(c.checkpoint_id)"
            />
            <button
              class="flex flex-shrink-0 items-center gap-1 rounded-lg border border-line-2 px-2.5 py-1.5 text-[11.5px] text-ink-dim transition hover:border-accent/50 hover:text-ink"
              @click="forkAt(c.checkpoint_id)"
            >
              <Icon name="refresh" :size="11" />
              从这步重跑
            </button>
          </div>
        </div>
      </div>
    </div>
  </Modal>
</template>
