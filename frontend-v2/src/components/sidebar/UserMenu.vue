<script setup lang="ts">
// 账户下拉菜单：个人主页 / 切换账号 / 退出登录
import { computed } from "vue";
import { useAuthStore } from "@/stores/auth";
import { useSessionsStore } from "@/stores/sessions";
import { useMemoryStore } from "@/stores/memory";
import { useDocsStore } from "@/stores/docs";
import { useChatStore } from "@/stores/chat";
import Dropdown from "@/components/common/Dropdown.vue";
import Icon from "@/components/common/Icon.vue";

const emit = defineEmits<{ profile: [] }>();
const auth = useAuthStore();
const sessions = useSessionsStore();
const memory = useMemoryStore();
const docs = useDocsStore();
const chat = useChatStore();

const initial = computed(() => (auth.user?.username || "?").slice(0, 1).toUpperCase());

async function afterLogout() {
  auth.logoutLocal();
  await Promise.all([sessions.load(), memory.load(), docs.load()]);
  if (!sessions.currentId) {
    if (sessions.list.length) {
      sessions.currentId = sessions.list[0].id;
      await chat.loadHistory(sessions.currentId);
    } else {
      await sessions.create();
      chat.clear();
    }
  }
}

function switchAccount() {
  // 仅打开登录框：关闭时保持当前登录态（不退出）；
  // 只有登录新账号或点"以访客身份继续"时才切换身份
  auth.openAuth("login");
}
</script>

<template>
  <Dropdown
    :open="auth.menuOpen"
    align="right"
    @close="auth.closeMenu()"
  >
    <template #trigger>
      <button
        v-if="auth.user"
        class="flex max-w-[140px] cursor-pointer items-center gap-1.5 rounded-full py-0.5 pl-0.5 pr-2 text-[11.5px] text-ink-dim transition hover:bg-surface-2 hover:text-ink"
        title="账户菜单"
        @click.stop="auth.toggleMenu()"
      >
        <span class="grid h-5 w-5 flex-shrink-0 place-items-center rounded-full bg-accent/20 text-[10px] font-semibold text-accent">
          {{ initial }}
        </span>
        <span class="truncate">{{ auth.user.username }}</span>
      </button>
    </template>
    <div class="flex items-center gap-2.5 border-b border-line px-3 py-2.5">
      <div class="grid h-9 w-9 flex-shrink-0 place-items-center rounded-full bg-accent/15 text-[13px] font-semibold text-accent">
        {{ initial }}
      </div>
      <div class="min-w-0">
        <div class="truncate text-[13px] font-semibold text-ink">{{ auth.user?.username }}</div>
        <div class="text-[11px] text-ink-faint">已登录</div>
      </div>
    </div>
    <div class="p-1">
      <button
        class="flex w-full items-center gap-2.5 rounded-md px-2.5 py-[7px] text-left text-[12.5px] text-ink transition hover:bg-surface-2"
        @click="auth.closeMenu(); emit('profile')"
      >
        <Icon name="user" :size="14" class="text-ink-dim" />
        个人主页
      </button>
      <button
        class="flex w-full items-center gap-2.5 rounded-md px-2.5 py-[7px] text-left text-[12.5px] text-ink transition hover:bg-surface-2"
        @click="auth.closeMenu(); switchAccount()"
      >
        <Icon name="switch" :size="14" class="text-ink-dim" />
        切换账号
      </button>
      <div class="mx-1 my-1 h-px bg-line" />
      <button
        class="flex w-full items-center gap-2.5 rounded-md px-2.5 py-[7px] text-left text-[12.5px] text-err transition hover:bg-err/10"
        @click="auth.closeMenu(); afterLogout()"
      >
        <Icon name="logout" :size="14" />
        退出登录
      </button>
    </div>
  </Dropdown>
</template>
