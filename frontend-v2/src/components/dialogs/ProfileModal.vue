<script setup lang="ts">
import { ref, watch } from "vue";
import Modal from "@/components/common/Modal.vue";
import Icon from "@/components/common/Icon.vue";
import { authApi } from "@/api";
import { useAuthStore } from "@/stores/auth";
import type { AuthStats, User } from "@/types/api";

const auth = useAuthStore();
const open = defineModel<boolean>({ default: false });
const me = ref<User | null>(null);
const st = ref<AuthStats | null>(null);
const error = ref("");

const initial = () => (auth.user?.username || "?").slice(0, 1).toUpperCase();

async function load() {
  error.value = "";
  try {
    const [m, s] = await Promise.all([authApi.me(), authApi.stats()]);
    me.value = m;
    st.value = s;
  } catch (e) {
    error.value = (e as Error).message;
  }
}

// 组件常驻挂载（App.vue 无 v-if），每次打开时重新加载，反映当前登录状态
watch(open, (v) => {
  if (v) load();
});
</script>

<template>
  <Modal :open="open" title="个人主页" @close="open = false">
    <div v-if="error" class="text-sm text-err">{{ error }}</div>
    <div v-else-if="st" class="flex flex-col gap-4">
      <!-- 横幅 -->
      <div class="flex items-center gap-3.5 rounded-xl border border-line bg-surface-2 px-4 py-3.5">
        <div class="grid h-12 w-12 flex-shrink-0 place-items-center rounded-full bg-accent/15 text-lg font-semibold text-accent">
          {{ initial() }}
        </div>
        <div class="min-w-0">
          <div class="flex items-center gap-2">
            <span class="text-[16px] font-semibold tracking-tight">{{ st.username }}</span>
            <span class="rounded-full bg-ok/12 px-2 py-0.5 text-[10px] font-medium text-ok">已登录</span>
          </div>
          <div class="mt-0.5 truncate font-mono text-[11px] text-ink-faint">{{ me?.id }}</div>
        </div>
      </div>
      <!-- 统计卡 -->
      <div class="grid grid-cols-4 gap-2">
        <div class="rounded-lg border border-line bg-surface-2 px-1 py-2.5 text-center">
          <b class="block text-[17px] font-semibold text-ink">{{ st.session_count }}</b>
          <span class="text-[10.5px] text-ink-faint">会话</span>
        </div>
        <div class="rounded-lg border border-line bg-surface-2 px-1 py-2.5 text-center">
          <b class="block text-[17px] font-semibold text-ink">{{ st.message_count }}</b>
          <span class="text-[10.5px] text-ink-faint">消息</span>
        </div>
        <div class="rounded-lg border border-line bg-surface-2 px-1 py-2.5 text-center">
          <b class="block text-[17px] font-semibold text-ink">{{ st.memory_count }}</b>
          <span class="text-[10.5px] text-ink-faint">记忆</span>
        </div>
        <div class="rounded-lg border border-line bg-surface-2 px-1 py-2.5 text-center">
          <b class="block text-[17px] font-semibold text-ink">{{ st.document_count }}</b>
          <span class="text-[10.5px] text-ink-faint">文档</span>
        </div>
      </div>
      <!-- 详情 -->
      <div class="overflow-hidden rounded-lg border border-line">
        <div class="flex items-center gap-2.5 bg-surface-2 px-3.5 py-2.5">
          <Icon name="clock" :size="14" class="flex-shrink-0 text-ink-faint" />
          <span class="flex-1 text-[12px] text-ink-faint">注册时间</span>
          <span class="text-[12px] text-ink-dim">{{ new Date(st.created_at).toLocaleString() }}</span>
        </div>
        <div class="flex items-center gap-2.5 border-t border-line bg-surface-2 px-3.5 py-2.5">
          <Icon name="key" :size="14" class="flex-shrink-0 text-ink-faint" />
          <span class="flex-1 text-[12px] text-ink-faint">用户 ID</span>
          <span class="max-w-[220px] break-all font-mono text-[11px] text-ink-dim">{{ me?.id }}</span>
        </div>
        <div class="flex items-center gap-2.5 border-t border-line bg-surface-2 px-3.5 py-2.5">
          <Icon name="stats" :size="14" class="flex-shrink-0 text-ink-faint" />
          <span class="flex-1 text-[12px] text-ink-faint">内容估算</span>
          <span class="text-[12px] text-ink-dim">约 {{ st.token_estimate.toLocaleString() }} tokens</span>
        </div>
      </div>
    </div>
  </Modal>
</template>
