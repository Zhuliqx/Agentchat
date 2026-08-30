<script setup lang="ts">
import { computed, reactive, ref } from "vue";
import Modal from "@/components/common/Modal.vue";
import Icon from "@/components/common/Icon.vue";
import EmptyState from "@/components/common/EmptyState.vue";
import { useTaskAgentStore, type TaskTraceItem } from "@/stores/taskAgent";
import { md } from "@/utils/markdown";

const store = useTaskAgentStore();
const goal = ref("");
const forkGoals = reactive<Record<string, string>>({});

// ---- HITL 编辑态 ----
const editMode = ref(false);
const editAction = ref("");
const editSource = ref("default");
const SOURCE_LABEL: Record<string, string> = {
  kb: "知识库",
  db: "数据库",
  web: "联网搜索",
  code: "代码执行",
  default: "默认",
};

const statusText = computed(() => {
  switch (store.status) {
    case "running":
      return "执行中";
    case "awaiting_confirm":
      return "待确认";
    case "done":
      return "已完成";
    case "error":
      return "出错";
    default:
      return "就绪";
  }
});
const statusCls = computed(() => {
  switch (store.status) {
    case "running":
      return "text-orbit";
    case "awaiting_confirm":
      return "text-warn";
    case "done":
      return "text-ok";
    case "error":
      return "text-err";
    default:
      return "text-ink-faint";
  }
});

const traceIcon = (kind: string) =>
  ({ plan: "sparkle", replan: "refresh", execute: "zap", check: "check", verify: "refresh", hitl: "warn", final: "check" })[kind] || "sparkle";
const traceCls = (t: TaskTraceItem) =>
  t.kind === "hitl"
    ? "text-warn"
    : t.kind === "execute" && t.ok === false
      ? "text-err"
      : t.kind === "final" || t.kind === "check"
        ? "text-ok"
        : "text-orbit";

async function start() {
  const text = goal.value.trim();
  if (!text) return;
  await store.run(text);
}

function beginEdit() {
  editMode.value = true;
  editAction.value = store.pending?.next_action || "";
  editSource.value = store.pending?.expected_source || "default";
}

async function submitEdit() {
  const action = editAction.value.trim();
  if (!action) return;
  editMode.value = false;
  await store.confirm("edit", action, editSource.value);
}

const copied = ref(false);
let copyTimer: ReturnType<typeof setTimeout> | null = null;
async function copyAnswer() {
  if (!store.finalAnswer) return;
  try {
    await navigator.clipboard.writeText(store.finalAnswer);
    copied.value = true;
    if (copyTimer) clearTimeout(copyTimer);
    copyTimer = setTimeout(() => (copied.value = false), 1600);
  } catch {
    /* 剪贴板不可用时静默忽略 */
  }
}
</script>

<template>
  <Modal :open="store.open" title="自主任务 Agent" @close="store.closeModal()">
    <div class="flex flex-col gap-3">
      <!-- 目标输入 -->
      <div class="rounded-xl border border-line bg-surface-2 p-3">
        <label class="mb-1.5 block text-[12px] text-ink-faint">目标（模糊长任务）</label>
        <textarea
          v-model="goal"
          rows="2"
          class="w-full resize-y rounded-lg border border-line-2 bg-surface px-3 py-2 text-[13px] leading-relaxed text-ink outline-none transition placeholder:text-ink-faint/60 focus:border-accent focus:ring-2 focus:ring-accent/15"
          placeholder="如：调研 RAG 主流方案，对比优缺点并给出选型建议…"
          @keydown.enter.exact.prevent="start"
        />
        <div class="mt-2 flex items-center justify-between gap-2">
          <span class="text-[11px]" :class="statusCls">{{ statusText }}</span>
          <div class="flex gap-2">
            <button
              v-if="store.running"
              class="flex items-center gap-1.5 rounded-lg border border-err/40 px-3 py-1.5 text-[12px] text-err transition hover:bg-err/10"
              @click="store.stop()"
            >
              <Icon name="stop" :size="12" />
              停止
            </button>
            <button
              v-else
              class="flex items-center gap-1.5 rounded-lg bg-accent px-3.5 py-1.5 text-[12px] font-medium text-white transition hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-40"
              :disabled="!goal.trim() || store.status === 'awaiting_confirm'"
              @click="start"
            >
              <Icon name="play" :size="12" />
              开始任务
            </button>
          </div>
        </div>
      </div>

      <!-- 执行轨迹 -->
      <div v-if="store.events.length" class="rounded-xl border border-line p-3">
        <div class="mb-2 text-[11px] font-medium uppercase tracking-wide text-ink-faint">
          执行轨迹
        </div>
        <div class="flex max-h-[180px] flex-col gap-1.5 overflow-y-auto pr-1">
          <div v-for="t in store.events" :key="t.id" class="flex items-start gap-2 text-[12px]">
            <Icon :name="traceIcon(t.kind)" :size="13" class="mt-0.5 flex-shrink-0" :class="traceCls(t)" />
            <span class="min-w-0 flex-1">
              <span class="font-medium text-ink-dim">{{ t.label }}</span>
              <span v-if="t.detail" class="ml-1.5 break-all text-ink-faint">{{ t.detail }}</span>
            </span>
            <span class="flex-shrink-0 text-[10px] text-ink-faint/70">{{ t.time }}</span>
          </div>
        </div>
      </div>

      <!-- HITL 人工确认 -->
      <div
        v-if="store.status === 'awaiting_confirm' && store.pending"
        class="rounded-xl border border-warn/25 bg-warn/5 p-3.5"
      >
        <div class="mb-2 flex items-start gap-2 text-[13px] text-ink">
          <Icon name="warn" :size="15" class="mt-0.5 flex-shrink-0 text-warn" />
          <span class="min-w-0 flex-1">
            确认下一步执行？
            <span class="mt-0.5 block break-all text-[12.5px] text-ink-dim">
              {{ store.pending.next_action || "（无动作）" }}
            </span>
            <span
              v-if="store.pending.expected_source"
              class="mt-1 inline-block rounded bg-surface-3 px-1.5 py-0.5 text-[10.5px] text-ink-faint"
            >
              信息源：{{ SOURCE_LABEL[store.pending.expected_source] || store.pending.expected_source }}
            </span>
          </span>
        </div>

        <!-- 编辑态 -->
        <div v-if="editMode" class="mt-2 flex flex-col gap-2">
          <input
            v-model="editAction"
            class="h-8 w-full rounded-lg border border-line-2 bg-surface px-2.5 text-[12px] text-ink outline-none focus:border-accent"
            placeholder="替换后的动作"
          />
          <div class="flex items-center gap-2">
            <select
              v-model="editSource"
              class="h-8 flex-1 rounded-lg border border-line-2 bg-surface px-2 text-[12px] text-ink outline-none focus:border-accent"
            >
              <option v-for="(label, key) in SOURCE_LABEL" :key="key" :value="key">{{ label }}</option>
            </select>
            <button
              class="h-8 rounded-lg bg-accent px-3 text-[12px] font-medium text-white transition hover:brightness-110 disabled:opacity-40"
              :disabled="!editAction.trim()"
              @click="submitEdit"
            >
              提交编辑
            </button>
            <button
              class="h-8 rounded-lg border border-line-2 px-3 text-[12px] text-ink-dim transition hover:text-ink"
              @click="editMode = false"
            >
              取消
            </button>
          </div>
        </div>

        <div v-else class="mt-2 flex flex-wrap gap-2">
          <button
            class="rounded-lg bg-ok px-3.5 py-1.5 text-[12px] font-medium text-white transition hover:brightness-110"
            @click="store.confirm('proceed')"
          >
            确认执行
          </button>
          <button
            class="rounded-lg border border-line-2 px-3.5 py-1.5 text-[12px] text-ink-dim transition hover:border-accent/50 hover:text-ink"
            @click="beginEdit"
          >
            编辑动作
          </button>
          <button
            class="rounded-lg border border-line-2 px-3.5 py-1.5 text-[12px] text-ink-dim transition hover:border-err/50 hover:text-err"
            @click="store.confirm('skip')"
          >
            跳过
          </button>
        </div>
      </div>

      <!-- 结果 -->
      <div v-if="store.plan || store.findings.length || store.finalAnswer" class="flex flex-col gap-3">
        <div v-if="store.plan" class="rounded-xl border border-line p-3">
          <div class="mb-1.5 text-[11px] font-medium uppercase tracking-wide text-ink-faint">计划</div>
          <div class="md text-[12.5px]" v-html="md.render(store.plan)" />
        </div>
        <div v-if="store.findings.length" class="rounded-xl border border-line p-3">
          <div class="mb-1.5 text-[11px] font-medium uppercase tracking-wide text-ink-faint">
            过程发现（{{ store.findings.length }}）
          </div>
          <div class="flex max-h-[160px] flex-col gap-1 overflow-y-auto pr-1">
            <div
              v-for="(f, i) in store.findings"
              :key="i"
              class="flex items-start gap-1.5 text-[12px] text-ink-dim"
            >
              <span class="mt-px flex-shrink-0 font-mono text-[10px] text-ink-faint">{{ i + 1 }}.</span>
              <span class="min-w-0 break-all leading-relaxed">{{ f }}</span>
            </div>
          </div>
        </div>
        <div v-if="store.finalAnswer" class="rounded-xl border border-line bg-surface-2/40 p-3">
          <div class="mb-1.5 flex items-center justify-between">
            <span class="text-[11px] font-medium uppercase tracking-wide text-ink-faint">最终答案</span>
            <button
              class="flex h-6 w-6 items-center justify-center rounded-md text-ink-faint transition hover:bg-surface-2 hover:text-ink"
              :title="copied ? '已复制' : '复制'"
              @click="copyAnswer"
            >
              <Icon :name="copied ? 'check' : 'copy'" :size="13" :class="copied ? 'text-ok' : ''" />
            </button>
          </div>
          <div class="md text-[13px]" v-html="md.render(store.finalAnswer)" />
        </div>
      </div>

      <div v-if="store.error" class="rounded-lg bg-err/10 px-3 py-2 text-[12.5px] text-err">
        {{ store.error }}
      </div>

      <!-- 版本历史（Time Travel） -->
      <div v-if="store.sessionId" class="rounded-xl border border-line">
        <div class="flex items-center justify-between border-b border-line px-3 py-2">
          <span class="flex items-center gap-1.5 text-[12px] font-medium text-ink-dim">
            <Icon name="clock" :size="13" class="text-ink-faint" />
            版本历史
          </span>
          <button
            class="flex items-center gap-1 rounded-md border border-line-2 px-2 py-1 text-[11px] text-ink-dim transition hover:border-accent/50 hover:text-ink"
            @click="store.loadHistory()"
          >
            <Icon name="refresh" :size="11" />
            加载
          </button>
        </div>
        <div v-if="store.history.length" class="max-h-[220px] overflow-y-auto p-2">
          <div v-for="(c, i) in store.history" :key="c.checkpoint_id" class="relative flex gap-2.5 pb-2">
            <div class="flex w-4 flex-shrink-0 flex-col items-center">
              <span
                class="mt-1 grid h-4 w-4 place-items-center rounded-full border border-line-2 bg-surface-2 text-[9px] font-medium text-ink-faint"
              >
                {{ store.history.length - i }}
              </span>
              <span v-if="i < store.history.length - 1" class="w-px flex-1 bg-line" />
            </div>
            <div class="min-w-0 flex-1 pb-1.5">
              <div class="flex flex-wrap items-center gap-1.5 text-[10.5px]">
                <span v-if="c.created_at" class="text-ink-faint">{{ new Date(c.created_at).toLocaleString() }}</span>
                <span
                  v-if="c.interrupted"
                  class="flex items-center gap-0.5 rounded-full bg-warn/10 px-1.5 py-px text-[10px] text-warn"
                >
                  <Icon name="warn" :size="9" />
                  待确认
                </span>
                <span
                  v-else-if="c.next?.length"
                  class="rounded-full bg-orbit/10 px-1.5 py-px text-[10px] text-orbit"
                >
                  下一步：{{ c.next.join(", ") }}
                </span>
                <span v-else class="rounded-full bg-ok/10 px-1.5 py-px text-[10px] text-ok">完成</span>
              </div>
              <div class="mt-0.5 line-clamp-2 break-all text-[11.5px] text-ink-dim">{{ c.summary || "（无文本步骤）" }}</div>
              <div class="mt-1 flex items-center gap-1.5">
                <input
                  v-model="forkGoals[c.checkpoint_id]"
                  class="h-6 min-w-0 flex-1 rounded-md border border-line-2 bg-surface-2 px-1.5 text-[11px] text-ink outline-none placeholder:text-ink-faint/60 focus:border-accent"
                  placeholder="新目标，从此处分叉…"
                  @keydown.enter="store.fork(c.checkpoint_id, c.checkpoint_ns || '', forkGoals[c.checkpoint_id])"
                />
                <button
                  class="h-6 flex-shrink-0 rounded-md border border-line-2 px-1.5 text-[10.5px] text-ink-dim transition hover:border-accent/50 hover:text-ink"
                  @click="store.fork(c.checkpoint_id, c.checkpoint_ns || '', forkGoals[c.checkpoint_id])"
                >
                  分叉
                </button>
                <button
                  class="h-6 flex-shrink-0 rounded-md border border-line-2 px-1.5 text-[10.5px] text-ink-dim transition hover:border-accent/50 hover:text-ink"
                  title="从该步继续执行"
                  @click="store.replay(c.checkpoint_id, c.checkpoint_ns || '')"
                >
                  重放
                </button>
              </div>
            </div>
          </div>
        </div>
        <EmptyState v-else text="暂无版本历史（执行过才有）" icon="clock" />
      </div>
    </div>
  </Modal>
</template>
