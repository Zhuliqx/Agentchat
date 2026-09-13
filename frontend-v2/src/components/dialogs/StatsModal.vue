<script setup lang="ts">
import { ref } from "vue";
import Modal from "@/components/common/Modal.vue";
import DialogShell from "@/components/common/DialogShell.vue";
import EmptyState from "@/components/common/EmptyState.vue";
import { sessionsApi } from "@/api";
import { useDialogResource } from "@/composables/useDialogResource";
import { useSessionsStore } from "@/stores/sessions";
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
</script>

<template>
  <Modal :open="open" title="会话数据分析" @close="open = false">
    <DialogShell :loading="loading" :error="error">
      <div v-if="st" class="flex flex-col gap-3">
        <div class="grid grid-cols-4 gap-2">
          <div class="rounded-lg border border-line bg-surface-2 px-1 py-2.5 text-center">
            <b class="block text-xl font-semibold text-ink">{{ st.message_count }}</b>
            <span class="text-2xs text-ink-faint">总消息数</span>
          </div>
          <div class="rounded-lg border border-line bg-surface-2 px-1 py-2.5 text-center">
            <b class="block text-xl font-semibold text-ink">{{ st.rounds }}</b>
            <span class="text-2xs text-ink-faint">对话回合</span>
          </div>
          <div class="rounded-lg border border-line bg-surface-2 px-1 py-2.5 text-center">
            <b class="block text-xl font-semibold text-ink"
              >{{ st.user_count }} / {{ st.assistant_count }}</b
            >
            <span class="text-2xs text-ink-faint">用户 / 助手</span>
          </div>
          <div class="rounded-lg border border-line bg-surface-2 px-1 py-2.5 text-center">
            <b class="block text-xl font-semibold text-ink">{{ st.est_tokens.toLocaleString() }}</b>
            <span class="text-2xs text-ink-faint">约 Token 数</span>
          </div>
        </div>
        <table class="w-full border-collapse text-xs">
          <tbody>
            <tr
              v-for="row in [
                ['内容总量', st.total_chars.toLocaleString() + ' 字符'],
                ['平均用户消息', st.avg_user_chars + ' 字符'],
                ['平均助手回复', st.avg_assistant_chars + ' 字符'],
                ['最长单次回复', st.longest_response_chars + ' 字符'],
                ['首次消息', fmtTime(st.first_at)],
                ['最后消息', fmtTime(st.last_at)],
                ['对话时长', fmtDur(st.duration_sec)],
              ]"
              :key="row[0]"
            >
              <td class="w-[38%] border-b border-line py-2 pr-2 text-ink-faint">{{ row[0] }}</td>
              <td class="border-b border-line py-2">{{ row[1] }}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <EmptyState v-else text="暂无会话数据" icon="stats" />
    </DialogShell>
  </Modal>
</template>
