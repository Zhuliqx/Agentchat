<script setup lang="ts">
import { computed, ref, watch } from "vue";
import Modal from "@/components/common/Modal.vue";
import Icon from "@/components/common/Icon.vue";
import { adminApi } from "@/api";
import type { AdminSettingItem } from "@/api";
import { useAuthStore } from "@/stores/auth";
import { avatarColor } from "@/utils/avatar";
import type { AdminStats, AdminUser, EvalCase } from "@/types/api";

const auth = useAuthStore();
const open = defineModel<boolean>({ default: false });
const stats = ref<AdminStats | null>(null);
const users = ref<AdminUser[]>([]);
const usage = ref<{
  items: { date: string; messages: number; tokens: number }[];
  total_messages: number;
  total_tokens: number;
} | null>(null);
const settingsItems = ref<AdminSettingItem[] | null>(null);
const settingsForm = ref<Record<string, string | number | boolean>>({});
const error = ref("");
// 管理后台分区折叠状态（localStorage 持久化）
const foldedPanel = ref<Record<string, boolean>>({
  settings: localStorage.getItem("admin-fold-settings") === "1",
  eval: localStorage.getItem("admin-fold-eval") === "1",
  users: localStorage.getItem("admin-fold-users") === "1",
});
function togglePanel(key: string) {
  foldedPanel.value[key] = !foldedPanel.value[key];
  localStorage.setItem(`admin-fold-${key}`, foldedPanel.value[key] ? "1" : "0");
}

async function load() {
  error.value = "";
  try {
    const [s, u, us, st, ev] = await Promise.all([
      adminApi.stats(),
      adminApi.users(),
      adminApi.usage(),
      adminApi.settings(),
      adminApi.eval(),
    ]);
    stats.value = s;
    users.value = u;
    usage.value = us;
    settingsItems.value = st.items;
    settingsForm.value = Object.fromEntries(st.items.map((i) => [i.key, i.value]));
    evalData.value = ev;
    customEdit.value = ev.custom.map((c) => ({
      query: c.query,
      keywordsText: (c.keywords || []).join("、"),
    }));
  } catch (e) {
    error.value = (e as Error).message;
  }
}

async function saveSettings() {
  try {
    const r = await adminApi.saveSettings(settingsForm.value);
    settingsItems.value = r.items;
    settingsForm.value = Object.fromEntries(r.items.map((i) => [i.key, i.value]));
    error.value = "";
  } catch (e) {
    error.value = (e as Error).message;
  }
}

function step(it: AdminSettingItem, dir: 1 | -1) {
  const cur = Number(settingsForm.value[it.key] ?? 0) || 0;
  const s = it.type === "float" ? 0.05 : 1;
  settingsForm.value[it.key] = Math.round((cur + dir * s) * 100) / 100;
}

function barHeight(n: number) {
  // 用固定 px 而非百分比：柱子父容器高度由内容撑开（auto），百分比高度会被
  // 浏览器解析为 0（循环依赖），导致柱子不显示。这里按最大消息数映射到 56px。
  const max = Math.max(...(usage.value?.items.map((i) => i.messages) || [1]), 1);
  return Math.max(3, Math.round((n / max) * 56)) + "px";
}

// ---- 知识库检索质量评估（后端统一执行：自动适配 + 内置 + 自定义） ----
const evalData = ref<{
  docs: { source: string; name: string }[];
  builtin: EvalCase[];
  auto: EvalCase[];
  custom: EvalCase[];
} | null>(null);
const evalResults = ref<{
  results: EvalCase[];
  hit: number;
  total: number;
  hit_rate: number | null;
} | null>(null);
const evalRunning = ref(false);
const evalError = ref("");
// 自定义案例编辑态（keywords 以顿号分隔字符串编辑）
const customEdit = ref<{ query: string; keywordsText: string }[]>([]);
const newQuery = ref("");
const newKeywords = ref("");

async function runEval() {
  evalRunning.value = true;
  evalError.value = "";
  evalResults.value = null;
  try {
    evalResults.value = await adminApi.runEval({
      include_auto: true,
      include_builtin: true,
    });
  } catch (e) {
    evalError.value = (e as Error).message;
  } finally {
    evalRunning.value = false;
  }
}

function addCustom() {
  const q = newQuery.value.trim();
  if (!q) return;
  customEdit.value.push({ query: q, keywordsText: newKeywords.value.trim() });
  newQuery.value = "";
  newKeywords.value = "";
}

function removeCustom(i: number) {
  customEdit.value.splice(i, 1);
}

async function saveCustom() {
  try {
    const r = await adminApi.saveEvalCases(
      customEdit.value.map((c) => ({
        query: c.query,
        keywords: c.keywordsText
          .split(/[,，、]/)
          .map((s) => s.trim())
          .filter(Boolean),
      }))
    );
    if (evalData.value) evalData.value.custom = r.custom;
  } catch (e) {
    evalError.value = (e as Error).message;
  }
}

const evalHitRate = computed(() => evalResults.value?.hit_rate ?? null);

watch(open, (v) => {
  if (v) load();
});

async function del(user: AdminUser) {
  if (!confirm(`确定删除用户「${user.username}」？将删除其全部会话、消息、记忆与知识库文档。`)) return;
  try {
    await adminApi.deleteUser(user.id);
    await load();
  } catch (e) {
    error.value = (e as Error).message;
  }
}

const initial = (u: AdminUser) => (u.username || "?").slice(0, 1).toUpperCase();
</script>

<template>
  <Modal :open="open" title="管理后台" @close="open = false">
    <div v-if="error" class="mb-3 rounded-lg bg-err/10 px-3 py-2 text-[12.5px] text-err">{{ error }}</div>
    <div v-if="stats" class="mb-4 grid grid-cols-4 gap-2">
      <div class="rounded-lg border border-line bg-surface-2 px-1 py-2.5 text-center">
        <b class="block text-[16px] font-semibold text-ink">{{ stats.user_count }}</b>
        <span class="text-[10.5px] text-ink-faint">用户</span>
      </div>
      <div class="rounded-lg border border-line bg-surface-2 px-1 py-2.5 text-center">
        <b class="block text-[16px] font-semibold text-ink">{{ stats.session_count }}</b>
        <span class="text-[10.5px] text-ink-faint">会话</span>
      </div>
      <div class="rounded-lg border border-line bg-surface-2 px-1 py-2.5 text-center">
        <b class="block text-[16px] font-semibold text-ink">{{ stats.message_count }}</b>
        <span class="text-[10.5px] text-ink-faint">消息</span>
      </div>
      <div class="rounded-lg border border-line bg-surface-2 px-1 py-2.5 text-center">
        <b class="block text-[16px] font-semibold text-ink">{{ stats.document_count }}</b>
        <span class="text-[10.5px] text-ink-faint">文档块</span>
      </div>
    </div>

    <!-- 用量趋势 -->
    <div v-if="usage" class="mb-4 rounded-lg border border-line p-3">
      <div class="mb-2 flex items-center justify-between">
        <span class="text-[12px] font-medium text-ink-dim">消息趋势（近 {{ usage.items.length }} 天）</span>
        <span class="text-[11px] text-ink-faint">
          共 {{ usage.total_messages.toLocaleString() }} 条 · 约 {{ usage.total_tokens.toLocaleString() }} tokens
        </span>
      </div>
      <div class="flex h-24 items-end justify-between gap-2">
        <div
          v-for="u in usage.items"
          :key="u.date"
          class="group flex h-full flex-1 flex-col items-center justify-end gap-1"
        >
          <span
            class="text-[9px] font-semibold tabular-nums text-accent opacity-0 transition group-hover:opacity-100"
          >
            {{ u.messages }}
          </span>
          <div
            class="w-2.5 rounded-t-full bg-linear-to-t from-accent/15 to-accent transition-all duration-200 group-hover:from-accent/40 group-hover:to-accent"
            :style="{ height: barHeight(u.messages) }"
            :title="`${u.date}: ${u.messages} 条`"
          />
          <span
            class="text-[9px] text-ink-faint transition group-hover:text-ink-dim"
          >
            {{ u.date.slice(5) }}
          </span>
        </div>
      </div>
    </div>

    <!-- 系统设置 -->
    <div v-if="settingsItems" class="mb-4 overflow-hidden rounded-lg border border-line">
      <div class="flex items-center justify-between border-b border-line bg-surface-2/40 px-3 py-2">
        <button
          class="flex items-center gap-1.5 text-[12px] font-medium text-ink-dim transition hover:text-ink"
          @click="togglePanel('settings')"
        >
          <Icon
            name="chevron"
            :size="12"
            class="transition-transform"
            :class="foldedPanel.settings ? '-rotate-90' : ''"
          />
          系统设置（检索 / 生成）
        </button>
        <button
          v-if="!foldedPanel.settings"
          class="rounded-md bg-accent px-2.5 py-1 text-[11.5px] font-medium text-white transition hover:brightness-110"
          @click="saveSettings"
        >
          保存
        </button>
      </div>
      <div v-if="!foldedPanel.settings" class="flex flex-col gap-2 p-3">
        <label
          v-for="it in settingsItems"
          :key="it.key"
          class="flex items-center justify-between gap-2 text-[12px] text-ink-dim"
        >
          <span>{{ it.label }}</span>
          <input
            v-if="it.type === 'bool'"
            type="checkbox"
            class="h-4 w-4 accent-[var(--color-accent)]"
            :checked="!!settingsForm[it.key]"
            @change="settingsForm[it.key] = !settingsForm[it.key]"
          />
          <div v-else class="group relative flex items-center">
            <input
              type="number"
              :step="it.type === 'float' ? '0.05' : '1'"
              class="h-7 w-12 rounded-md border border-line-2 bg-surface px-2 text-center text-[12px] text-ink outline-none transition focus:border-accent focus:ring-2 focus:ring-accent/10 [appearance:textfield] [&::-webkit-inner-spin-button]:appearance-none [&::-webkit-outer-spin-button]:appearance-none"
              v-model.number="settingsForm[it.key]"
            />
            <!-- stepper：悬浮在输入框左侧外，hover/聚焦时淡入 -->
            <div
              class="absolute right-full top-1/2 z-10 flex -translate-y-1/2 flex-col overflow-hidden rounded-md border border-line bg-surface opacity-0 shadow-md transition-opacity duration-150 group-hover:opacity-100 group-focus-within:opacity-100"
            >
              <button
                type="button"
                tabindex="-1"
                class="grid h-3.5 w-5 place-items-center text-ink-faint transition hover:bg-accent/10 hover:text-accent"
                title="增大"
                @click="step(it, 1)"
              >
                <Icon name="chevron" :size="8" class="rotate-180" />
              </button>
              <button
                type="button"
                tabindex="-1"
                class="grid h-3.5 w-5 place-items-center border-t border-line text-ink-faint transition hover:bg-accent/10 hover:text-accent"
                title="减小"
                @click="step(it, -1)"
              >
                <Icon name="chevron" :size="8" />
              </button>
            </div>
          </div>
        </label>
      </div>
    </div>

    <!-- 知识库检索质量评估 -->
    <div class="mb-4 overflow-hidden rounded-lg border border-line">
      <div class="flex items-center justify-between border-b border-line bg-surface-2/40 px-3 py-2">
        <button
          class="flex items-center gap-1.5 text-[12px] font-medium text-ink-dim transition hover:text-ink"
          @click="togglePanel('eval')"
        >
          <Icon
            name="chevron"
            :size="12"
            class="transition-transform"
            :class="foldedPanel.eval ? '-rotate-90' : ''"
          />
          知识库检索质量评估
        </button>
        <button
          v-if="!foldedPanel.eval"
          class="flex items-center gap-1 rounded-md bg-accent px-2.5 py-1 text-[11.5px] font-medium text-white shadow-sm transition hover:brightness-110 disabled:opacity-50"
          :disabled="evalRunning"
          @click="runEval"
        >
          <Icon v-if="evalRunning" name="refresh" :size="11" class="animate-spin" />
          {{ evalRunning ? "评估中…" : "运行评估" }}
        </button>
      </div>

      <div v-if="!foldedPanel.eval" class="flex flex-col gap-3 p-3">
        <div v-if="evalError" class="rounded-md bg-err/10 px-2 py-1.5 text-[11px] text-err">
          {{ evalError }}
        </div>

        <!-- 知识库文档 -->
        <div v-if="evalData?.docs.length">
          <div class="mb-1.5 flex items-center gap-1 text-[10px] font-medium uppercase tracking-wide text-ink-faint">
            知识库文档
            <span class="rounded bg-surface-3 px-1 py-px text-[9px] font-normal">{{ evalData.docs.length }}</span>
          </div>
          <div class="flex flex-wrap gap-1">
            <span
              v-for="d in evalData.docs"
              :key="d.source"
              class="flex items-center gap-1 rounded-md border border-line bg-surface-2 px-1.5 py-0.5 text-[10.5px] text-ink-dim"
            >
              <Icon name="doc" :size="10" class="text-accent" />
              {{ d.name }}
            </span>
          </div>
        </div>

        <!-- 自动适配案例 -->
        <div v-if="evalData?.auto.length">
          <div class="mb-1.5 flex items-center gap-1 text-[10px] font-medium uppercase tracking-wide text-ink-faint">
            自动适配案例
            <span class="rounded bg-accent/10 px-1 py-px text-[9px] font-normal text-accent">{{ evalData.auto.length }}</span>
            <span class="ml-auto font-normal normal-case tracking-normal">验证每个文档能否被检索</span>
          </div>
          <div class="flex flex-col gap-0.5 rounded-md border border-line bg-surface-2/30 px-1.5 py-1">
            <div v-for="(c, i) in evalData.auto" :key="i" class="flex items-center gap-1.5 py-0.5 text-[11px] text-ink-dim">
              <Icon name="sparkle" :size="10" class="flex-shrink-0 text-accent/70" />
              <span class="truncate">「{{ c.query }}」</span>
              <span class="ml-auto flex-shrink-0 truncate rounded bg-surface-3 px-1 py-px text-[9px] text-ink-faint">{{ c.doc }}</span>
            </div>
          </div>
        </div>

        <!-- 自定义案例（可编辑） -->
        <div>
          <div class="mb-1.5 flex items-center gap-1 text-[10px] font-medium uppercase tracking-wide text-ink-faint">
            自定义案例
            <span class="rounded bg-accent/10 px-1 py-px text-[9px] font-normal text-accent">{{ customEdit.length }}</span>
            <span class="ml-auto font-normal normal-case tracking-normal">命中 = 检索结果含关键词</span>
          </div>
          <div class="flex flex-col gap-1">
            <div v-for="(c, i) in customEdit" :key="i" class="flex items-center gap-1">
              <input
                v-model="c.query"
                class="h-6 min-w-0 flex-1 rounded-md border border-line-2 bg-surface px-1.5 text-[11px] text-ink outline-none transition focus:border-accent focus:ring-2 focus:ring-accent/10"
                placeholder="问题"
              />
              <input
                v-model="c.keywordsText"
                class="h-6 w-[46%] rounded-md border border-line-2 bg-surface px-1.5 text-[11px] text-ink outline-none transition focus:border-accent focus:ring-2 focus:ring-accent/10"
                placeholder="关键词（顿号分隔）"
              />
              <button
                class="grid h-6 w-6 flex-shrink-0 place-items-center rounded-md text-ink-faint transition hover:bg-err/10 hover:text-err"
                title="删除"
                @click="removeCustom(i)"
              >
                <Icon name="x" :size="11" />
              </button>
            </div>
            <div class="flex items-center gap-1">
              <input
                v-model="newQuery"
                class="h-6 min-w-0 flex-1 rounded-md border border-dashed border-line-2 bg-surface px-1.5 text-[11px] text-ink outline-none transition placeholder:text-ink-faint/50 focus:border-accent"
                placeholder="新问题…"
                @keyup.enter="addCustom"
              />
              <input
                v-model="newKeywords"
                class="h-6 w-[46%] rounded-md border border-dashed border-line-2 bg-surface px-1.5 text-[11px] text-ink outline-none transition placeholder:text-ink-faint/50 focus:border-accent"
                placeholder="关键词…"
                @keyup.enter="addCustom"
              />
              <button
                class="grid h-6 w-6 flex-shrink-0 place-items-center rounded-md text-ink-faint transition hover:bg-accent/10 hover:text-accent"
                title="添加"
                @click="addCustom"
              >
                <Icon name="plus" :size="11" />
              </button>
            </div>
          </div>
          <button
            v-if="customEdit.length"
            class="mt-1.5 rounded-md border border-line-2 px-2 py-0.5 text-[10.5px] text-ink-dim transition hover:border-accent/50 hover:text-ink"
            @click="saveCustom"
          >
            保存自定义案例
          </button>
        </div>

        <!-- 评估结果 -->
        <div v-if="evalResults" class="rounded-md border border-line bg-surface-2/30 p-2">
          <div v-for="(r, i) in evalResults.results" :key="i" class="flex items-center gap-1.5 py-1 text-[11px]">
            <span
              class="grid h-4 w-4 flex-shrink-0 place-items-center rounded-full text-[9px] font-bold"
              :class="r.hit ? 'bg-ok/15 text-ok' : 'bg-err/15 text-err'"
            >
              {{ r.hit ? "✓" : "✗" }}
            </span>
            <span class="min-w-0 flex-1 truncate text-ink-dim">{{ r.query }}</span>
            <span v-if="r.doc" class="max-w-[30%] flex-shrink-0 truncate text-[9.5px] text-ink-faint">{{ r.doc }}</span>
            <span class="flex-shrink-0 rounded bg-surface-3 px-1 py-px text-[9.5px] text-ink-faint">{{ r.hits }} 条</span>
          </div>
          <div class="mt-1 flex items-center justify-between border-t border-line pt-1.5">
            <span class="text-[11px] text-ink-faint">{{ evalResults.hit }}/{{ evalResults.total }} 通过</span>
            <span
              class="text-[12.5px] font-semibold tabular-nums"
              :class="(evalHitRate ?? 0) >= 75 ? 'text-ok' : 'text-warn'"
            >
              命中率 {{ evalHitRate }}%
            </span>
          </div>
        </div>
        <div
          v-else-if="!evalRunning"
          class="rounded-md border border-dashed border-line px-2 py-2.5 text-center text-[11px] text-ink-faint/80"
        >
          自动从知识库文档生成案例 + 自定义案例，后端跑完整检索链路（向量 + BM25 + Rerank）验证召回质量。
        </div>
      </div>
    </div>

    <div class="mb-4 overflow-hidden rounded-lg border border-line">
      <div class="flex items-center justify-between border-b border-line bg-surface-2/40 px-3 py-2">
        <button
          class="flex items-center gap-1.5 text-[12px] font-medium text-ink-dim transition hover:text-ink"
          @click="togglePanel('users')"
        >
          <Icon
            name="chevron"
            :size="12"
            class="transition-transform"
            :class="foldedPanel.users ? '-rotate-90' : ''"
          />
          用户列表
        </button>
        <span v-if="!foldedPanel.users" class="text-[11px] text-ink-faint">{{ users.length }} 个</span>
      </div>
      <div v-if="!foldedPanel.users" class="flex flex-col gap-px p-2">
      <div
        v-for="u in users"
        :key="u.id"
        class="flex items-center gap-2.5 rounded-md px-1.5 py-2 text-[12px] transition hover:bg-surface-2"
      >
        <span
          class="grid h-7 w-7 flex-shrink-0 place-items-center rounded-full text-[11px] font-semibold"
          :class="[avatarColor(u).bg, avatarColor(u).text]"
        >
          {{ initial(u) }}
        </span>
        <div class="min-w-0 flex-1">
          <div class="flex items-center gap-1.5">
            <span class="truncate font-medium text-ink">{{ u.username }}</span>
            <span
              v-if="u.is_admin"
              class="rounded-full bg-accent/12 px-1.5 py-px text-[9.5px] font-medium text-accent"
            >管理员</span>
            <span
              v-if="u.id === auth.user?.id"
              class="rounded-full bg-ok/12 px-1.5 py-px text-[9.5px] font-medium text-ok"
            >我</span>
          </div>
          <div class="mt-0.5 text-[10.5px] text-ink-faint">
            会话 {{ u.session_count }} · 消息 {{ u.message_count }} · 文档 {{ u.document_count }}
          </div>
        </div>
        <span class="flex-shrink-0 text-[10.5px] text-ink-faint">{{ new Date(u.created_at).toLocaleDateString() }}</span>
        <button
          class="flex h-6 w-6 flex-shrink-0 items-center justify-center rounded-md text-ink-faint transition hover:bg-err/10 hover:text-err disabled:cursor-not-allowed disabled:opacity-30"
          title="删除用户"
          :disabled="u.id === auth.user?.id"
          @click="del(u)"
        >
          <Icon name="trash" :size="12" />
        </button>
      </div>
    </div>
    </div>
  </Modal>
</template>
