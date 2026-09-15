<script setup lang="ts">
import { computed, ref } from "vue";
import Modal from "@/components/common/Modal.vue";
import DialogShell from "@/components/common/DialogShell.vue";
import EmptyState from "@/components/common/EmptyState.vue";
import Icon from "@/components/common/Icon.vue";
import { sessionsApi } from "@/api";
import { useDialogResource } from "@/composables/useDialogResource";
import { useSessionsStore } from "@/stores/sessions";
import { sourceName } from "@/utils/sources";
import type { SessionStats } from "@/types/api";

const open = defineModel<boolean>({ default: false });
const sessions = useSessionsStore();
const st = ref<SessionStats | null>(null);

const { loading, error } = useDialogResource(
  open,
  async () => {
    st.value = sessions.currentId ? await sessionsApi.stats(sessions.currentId) : null;
  },
  { key: () => sessions.currentId },
);

function fmtDur(sec: number | null) {
  if (sec == null) return "—";
  if (sec < 60) return `${sec} 秒`;
  if (sec < 3600) return `${Math.floor(sec / 60)} 分 ${sec % 60} 秒`;
  return `${Math.floor(sec / 3600)} 时 ${Math.floor((sec % 3600) / 60)} 分`;
}
function fmtTime(iso: string | null) {
  return iso ? new Date(iso).toLocaleString() : "—";
}
/** 应答耗时：小于 10 秒保留一位小数，超过 1 分钟交给 fmtDur */
function fmtSeconds(sec: number | null | undefined) {
  if (sec == null) return "—";
  if (sec >= 60) return fmtDur(Math.round(sec));
  return `${sec < 10 ? sec.toFixed(1) : Math.round(sec)} 秒`;
}

/** 概览四张卡：数值 + 一句话补充，避免只有裸数字 */
const kpis = computed(() => {
  const s = st.value;
  if (!s) return [];
  return [
    {
      icon: "chat",
      label: "消息总数",
      value: String(s.message_count),
      sub: `${s.rounds} 个回合 · 系统 ${s.system_count}`,
    },
    {
      icon: "orbit",
      label: "对话回合",
      value: String(s.rounds),
      sub: `用户 ${s.user_count} · 助手 ${s.assistant_count}`,
    },
    {
      icon: "zap",
      label: "约 Token",
      value: s.est_tokens.toLocaleString(),
      sub: `${s.total_chars.toLocaleString()} 字符`,
    },
    {
      icon: "clock",
      label: "对话时长",
      value: fmtDur(s.duration_sec),
      sub: `均答 ${fmtSeconds(s.avg_response_sec)}`,
    },
  ];
});

/** 消息构成：环形图分段（用户 / 助手 / 系统） */
const composition = computed(() => {
  const s = st.value;
  if (!s) return [];
  const total = Math.max(1, s.message_count);
  let offset = 0;
  return [
    { label: "用户", count: s.user_count, color: "var(--color-accent)" },
    { label: "助手", count: s.assistant_count, color: "var(--color-orbit)" },
    { label: "系统", count: s.system_count, color: "var(--color-ink-faint)" },
  ]
    .filter((part) => part.count > 0)
    .map((part) => {
      const pct = (part.count / total) * 100;
      const seg = { ...part, pct, dash: `${pct} ${100 - pct}`, offset: -offset };
      offset += pct;
      return seg;
    });
});

/** 长度画像：三条横向对比（相对最长者缩放） */
const lengths = computed(() => {
  const s = st.value;
  if (!s) return [];
  const items = [
    { label: "平均用户", value: s.avg_user_chars, color: "var(--color-accent)" },
    { label: "平均助手", value: s.avg_assistant_chars, color: "var(--color-orbit)" },
    { label: "最长回复", value: s.longest_response_chars, color: "var(--color-ok)" },
  ];
  const max = Math.max(...items.map((i) => i.value), 1);
  return items.map((i) => ({ ...i, pct: Math.max(3, Math.round((i.value / max) * 100)) }));
});

/** 活跃时段：24 根柱子，最活跃的一根高亮 */
const hourly = computed(() => {
  const counts = st.value?.hourly_counts ?? [];
  if (!counts.length) return [];
  const max = Math.max(...counts, 1);
  const peak = counts.indexOf(max);
  return counts.map((count, hour) => ({
    hour,
    count,
    pct: count ? Math.max(8, Math.round((count / max) * 100)) : 0,
    isPeak: count > 0 && hour === peak,
  }));
});
const peakLabel = computed(() => {
  const counts = st.value?.hourly_counts ?? [];
  const max = Math.max(...counts, 0);
  if (!max) return "";
  return `${String(counts.indexOf(max)).padStart(2, "0")}:00 前后最活跃`;
});

/** 引用来源 Top：按命中片段数排序，条宽相对第一名的命中数 */
const sources = computed(() => {
  const list = st.value?.top_sources ?? [];
  const max = Math.max(...list.map((s) => s.hits || s.messages), 1);
  return list.map((s) => ({
    ...s,
    name: sourceName(s.path),
    pct: Math.max(6, Math.round(((s.hits || s.messages) / max) * 100)),
  }));
});
</script>

<template>
  <Modal :open="open" title="会话数据分析" @close="open = false">
    <DialogShell :loading="loading" :error="error">
      <div v-if="st" data-testid="stats-body" class="flex flex-col gap-3.5">
        <!-- 时间范围 + 活跃峰值 -->
        <div class="flex flex-wrap items-center gap-x-2 gap-y-1 text-2xs text-ink-faint">
          <span>{{ fmtTime(st.first_at) }}</span>
          <Icon name="chevron" :size="10" class="-rotate-90" />
          <span>{{ fmtTime(st.last_at) }}</span>
          <span
            v-if="peakLabel"
            class="ml-auto rounded-full border border-line px-2 py-0.5 text-ink-dim"
          >
            {{ peakLabel }}
          </span>
        </div>

        <!-- 概览 -->
        <div class="grid grid-cols-2 gap-2 sm:grid-cols-4">
          <div
            v-for="k in kpis"
            :key="k.label"
            class="rounded-xl border border-line bg-surface-2/50 px-2.5 py-2"
          >
            <div class="flex items-center gap-1 text-2xs text-ink-faint">
              <Icon :name="k.icon" :size="12" />
              {{ k.label }}
            </div>
            <b class="mt-1 block truncate text-xl font-semibold leading-tight text-ink">
              {{ k.value }}
            </b>
            <span class="block truncate text-2xs text-ink-faint">{{ k.sub }}</span>
          </div>
        </div>

        <div class="grid gap-2 sm:grid-cols-2">
          <!-- 消息构成 -->
          <section
            data-testid="stats-composition"
            class="rounded-xl border border-line bg-surface-2/40 p-3"
          >
            <h3 class="mb-2 text-2xs font-medium uppercase tracking-[0.08em] text-ink-faint">
              消息构成
            </h3>
            <div class="flex items-center gap-4">
              <svg
                viewBox="0 0 42 42"
                class="h-20 w-20 flex-shrink-0 -rotate-90"
                role="img"
                aria-label="消息构成"
              >
                <circle
                  cx="21"
                  cy="21"
                  r="15.9"
                  fill="none"
                  stroke="var(--color-surface-3)"
                  stroke-width="5"
                />
                <circle
                  v-for="seg in composition"
                  :key="seg.label"
                  cx="21"
                  cy="21"
                  r="15.9"
                  fill="none"
                  :stroke="seg.color"
                  stroke-width="5"
                  path-length="100"
                  :stroke-dasharray="seg.dash"
                  :stroke-dashoffset="seg.offset"
                />
              </svg>
              <ul class="flex min-w-0 flex-1 flex-col gap-1.5 text-xs">
                <li v-for="seg in composition" :key="seg.label" class="flex items-center gap-2">
                  <span
                    class="h-2 w-2 flex-shrink-0 rounded-full"
                    :style="{ background: seg.color }"
                  />
                  <span class="text-ink-dim">{{ seg.label }}</span>
                  <span class="ml-auto tabular-nums text-ink">{{ seg.count }}</span>
                  <span class="w-9 text-right tabular-nums text-ink-faint">
                    {{ Math.round(seg.pct) }}%
                  </span>
                </li>
              </ul>
            </div>
          </section>

          <!-- 长度画像 -->
          <section
            data-testid="stats-lengths"
            class="rounded-xl border border-line bg-surface-2/40 p-3"
          >
            <h3 class="mb-2 text-2xs font-medium uppercase tracking-[0.08em] text-ink-faint">
              长度画像（字符）
            </h3>
            <div class="flex flex-col gap-2.5">
              <div v-for="item in lengths" :key="item.label">
                <div class="flex items-baseline justify-between text-2xs">
                  <span class="text-ink-dim">{{ item.label }}</span>
                  <span class="tabular-nums text-ink">{{ item.value }}</span>
                </div>
                <div class="mt-1 h-1.5 overflow-hidden rounded-full bg-surface-3">
                  <span
                    class="block h-full rounded-full"
                    :style="{ width: item.pct + '%', background: item.color }"
                  />
                </div>
              </div>
            </div>
          </section>
        </div>

        <!-- 活跃时段 -->
        <section
          v-if="hourly.length"
          data-testid="stats-hourly"
          class="rounded-xl border border-line bg-surface-2/40 p-3"
        >
          <h3 class="mb-2 text-2xs font-medium uppercase tracking-[0.08em] text-ink-faint">
            活跃时段
          </h3>
          <div class="flex h-14 items-end gap-[3px]">
            <span
              v-for="h in hourly"
              :key="h.hour"
              class="flex-1 rounded-sm"
              :class="h.isPeak ? 'bg-accent' : h.count ? 'bg-accent/35' : 'bg-surface-3'"
              :style="{ height: (h.pct || 2) + '%' }"
              :title="`${h.hour}:00 · ${h.count} 条`"
            />
          </div>
          <div class="mt-1 flex justify-between text-2xs text-ink-faint">
            <span>00</span><span>06</span><span>12</span><span>18</span><span>23</span>
          </div>
        </section>

        <!-- 引用来源 -->
        <section
          v-if="sources.length"
          data-testid="stats-sources"
          class="rounded-xl border border-line bg-surface-2/40 p-3"
        >
          <h3 class="mb-2 text-2xs font-medium uppercase tracking-[0.08em] text-ink-faint">
            引用来源 Top {{ sources.length }}
          </h3>
          <div class="flex flex-col gap-2">
            <div v-for="s in sources" :key="s.path" class="flex items-center gap-2 text-xs">
              <span class="min-w-0 flex-1 truncate text-ink-dim" :title="s.path">{{ s.name }}</span>
              <span class="hidden w-20 flex-shrink-0 sm:block">
                <span
                  class="block h-1.5 rounded-full bg-orbit/70"
                  :style="{ width: s.pct + '%' }"
                />
              </span>
              <span class="w-24 flex-shrink-0 text-right tabular-nums text-ink-faint">
                {{ s.hits }} 命中 / {{ s.messages }} 次
              </span>
            </div>
          </div>
        </section>

        <!-- 细项 -->
        <div class="flex flex-wrap gap-x-4 gap-y-1 text-2xs text-ink-faint">
          <span
            >内容总量
            <b class="font-medium text-ink-dim">{{ st.total_chars.toLocaleString() }}</b> 字符</span
          >
          <span
            >平均应答
            <b class="font-medium text-ink-dim">{{ fmtSeconds(st.avg_response_sec) }}</b></span
          >
          <span
            >最长单次回复
            <b class="font-medium text-ink-dim">{{ st.longest_response_chars }}</b> 字符</span
          >
        </div>
      </div>
      <EmptyState v-else text="暂无会话数据" icon="stats" />
    </DialogShell>
  </Modal>
</template>
