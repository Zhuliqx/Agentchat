<script setup lang="ts">
import { ref, watch } from "vue";
import Modal from "@/components/common/Modal.vue";
import Icon from "@/components/common/Icon.vue";
import { authApi } from "@/api";
import { useAuthStore } from "@/stores/auth";
import { useSessionsStore } from "@/stores/sessions";
import { useMemoryStore } from "@/stores/memory";
import { useDocsStore } from "@/stores/docs";
import { useChatStore } from "@/stores/chat";
import { setStoredUser } from "@/api/token";
import { AVATAR_COLORS, AVATAR_ORDER, avatarColor } from "@/utils/avatar";
import type { AuthStats, User } from "@/types/api";

const auth = useAuthStore();
const open = defineModel<boolean>({ default: false });
const me = ref<User | null>(null);
const st = ref<AuthStats | null>(null);
const error = ref("");
const profileForm = ref({ username: "" });
const pwdForm = ref({ oldPassword: "", newPassword: "" });
const saving = ref(false);
const formMsg = ref("");
const formOk = ref(true);
const showOld = ref(false);
const showNew = ref(false);
const selectedColor = ref(auth.user?.avatar_color || "accent");

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
  if (v) {
    load();
    formMsg.value = "";
    formOk.value = true;
    showOld.value = false;
    showNew.value = false;
    profileForm.value = { username: "" };
    pwdForm.value = { oldPassword: "", newPassword: "" };
    selectedColor.value = auth.user?.avatar_color || "accent";
  }
});

async function saveProfile() {
  const username = profileForm.value.username.trim();
  if (!username) return;
  saving.value = true;
  formMsg.value = "";
  try {
    const u = await authApi.updateProfile({ username });
    auth.user = u;
    setStoredUser(u);
    me.value = u;
    if (st.value) st.value.username = u.username;
    profileForm.value.username = "";
    formMsg.value = "用户名已更新";
    formOk.value = true;
  } catch (e) {
    formMsg.value = (e as Error).message;
    formOk.value = false;
  } finally {
    saving.value = false;
  }
}

async function savePassword() {
  if (pwdForm.value.newPassword.length < 6) {
    formMsg.value = "新密码至少 6 位";
    formOk.value = false;
    return;
  }
  saving.value = true;
  formMsg.value = "";
  try {
    await authApi.changePassword(pwdForm.value.oldPassword, pwdForm.value.newPassword);
    pwdForm.value = { oldPassword: "", newPassword: "" };
    showOld.value = false;
    showNew.value = false;
    formMsg.value = "密码已更新";
    formOk.value = true;
  } catch (e) {
    formMsg.value = (e as Error).message;
    formOk.value = false;
  } finally {
    saving.value = false;
  }
}

/** 点击色板圆点立即保存头像颜色（无需额外提交）。 */
async function pickColor(key: string) {
  if (selectedColor.value === key) return;
  selectedColor.value = key;
  formMsg.value = "";
  try {
    const u = await authApi.updateProfile({ avatar_color: key });
    auth.user = u;
    setStoredUser(u);
    formMsg.value = "头像颜色已更新";
    formOk.value = true;
  } catch (e) {
    selectedColor.value = auth.user?.avatar_color || "accent";
    formMsg.value = (e as Error).message;
    formOk.value = false;
  }
}

async function exportData() {
  try {
    const data = await authApi.exportData();
    const blob = new Blob([JSON.stringify(data, null, 2)], {
      type: "application/json",
    });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `agentchat-export-${new Date()
      .toISOString()
      .slice(0, 19)
      .replace(/[:T]/g, "-")}.json`;
    a.click();
    URL.revokeObjectURL(url);
    formMsg.value = "数据已导出";
    formOk.value = true;
  } catch (e) {
    formMsg.value = (e as Error).message;
    formOk.value = false;
  }
}

async function deleteAccount() {
  const username = auth.user?.username || "";
  const input = prompt(`请输入用户名「${username}」以确认注销：`);
  if (!input) return;
  if (input !== username) {
    formMsg.value = "用户名不匹配，已取消注销";
    formOk.value = false;
    return;
  }
  if (
    !confirm("注销将永久删除该账号的全部会话、消息、记忆与知识库文档，且不可恢复。确定继续？")
  )
    return;
  try {
    await authApi.deleteAccount();
    auth.logoutLocal();
    open.value = false;
    // 切回访客域（与退出登录一致）：重置会话与聊天区，避免残留
    const sessions = useSessionsStore();
    const memory = useMemoryStore();
    const docs = useDocsStore();
    const chat = useChatStore();
    sessions.currentId = "";
    chat.clear();
    await Promise.all([sessions.load(), memory.load(), docs.load()]);
    if (sessions.list.length) {
      sessions.currentId = sessions.list[0].id;
      await chat.loadHistory(sessions.currentId);
    } else {
      await sessions.create();
    }
    alert("账号已注销");
  } catch (e) {
    formMsg.value = (e as Error).message;
    formOk.value = false;
  }
}
</script>

<template>
  <Modal :open="open" title="个人主页" @close="open = false">
    <div v-if="error" class="text-sm text-err">{{ error }}</div>
    <div v-else-if="st" class="flex flex-col gap-4">
      <!-- 横幅 -->
      <div class="flex items-center gap-3.5 rounded-xl border border-line bg-surface-2 px-4 py-3.5">
        <div
          class="grid h-12 w-12 flex-shrink-0 place-items-center rounded-full text-lg font-semibold"
          :class="[avatarColor(auth.user).bg, avatarColor(auth.user).text]"
        >
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
      <!-- 资料 -->
      <div class="overflow-hidden rounded-xl border border-line">
        <div class="flex items-center gap-2 border-b border-line bg-surface-2 px-4 py-2.5">
          <Icon name="user" :size="14" class="flex-shrink-0 text-accent" />
          <span class="text-[12.5px] font-medium">资料</span>
        </div>
        <div class="flex flex-col divide-y divide-line">
          <!-- 头像颜色 -->
          <div class="p-4">
            <label class="flex items-center gap-1.5 text-[12.5px] font-medium text-ink">
              <Icon name="user" :size="13" class="text-ink-faint" />
              头像颜色
            </label>
            <div class="mt-2.5 flex items-center gap-2.5">
              <button
                v-for="key in AVATAR_ORDER"
                :key="key"
                type="button"
                class="grid h-8 w-8 place-items-center rounded-full transition hover:scale-110 active:scale-95"
                :class="selectedColor === key ? 'ring-2 ring-ink-dim ring-offset-2 ring-offset-surface' : ''"
                :title="AVATAR_COLORS[key].label"
                @click="pickColor(key)"
              >
                <span class="h-6 w-6 rounded-full" :class="AVATAR_COLORS[key].dot" />
              </button>
            </div>
          </div>
          <!-- 用户名 -->
          <div class="p-4">
            <label class="flex items-center gap-1.5 text-[12.5px] font-medium text-ink">
              <Icon name="edit" :size="13" class="text-ink-faint" />
              用户名
            </label>
            <p class="mt-1 text-[11.5px] text-ink-faint">当前用户名：{{ st.username }}</p>
            <div class="mt-2.5 flex items-center gap-2">
              <input
                v-model="profileForm.username"
                placeholder="输入新用户名"
                maxlength="32"
                class="h-9 min-w-0 flex-1 rounded-lg border border-line-2 bg-surface px-3 text-[13px] text-ink outline-none transition placeholder:text-ink-faint/70 focus:border-accent focus:ring-2 focus:ring-accent/15"
              />
              <button
                :disabled="saving || !profileForm.username.trim()"
                class="h-9 shrink-0 rounded-lg bg-accent px-4 text-[13px] font-medium text-white transition hover:brightness-110 active:scale-[0.98] disabled:cursor-not-allowed disabled:opacity-40"
                @click="saveProfile"
              >
                {{ saving ? "保存中…" : "保存" }}
              </button>
            </div>
          </div>
        </div>
      </div>
      <!-- 安全 -->
      <div class="overflow-hidden rounded-xl border border-line">
        <div class="flex items-center gap-2 border-b border-line bg-surface-2 px-4 py-2.5">
          <Icon name="shield" :size="14" class="flex-shrink-0 text-accent" />
          <span class="text-[12.5px] font-medium">安全</span>
        </div>
        <div class="p-4">
          <label class="flex items-center gap-1.5 text-[12.5px] font-medium text-ink">
            <Icon name="key" :size="13" class="text-ink-faint" />
            修改密码
          </label>
          <div class="mt-2.5 flex flex-col gap-2">
            <div class="relative">
              <input
                v-model="pwdForm.oldPassword"
                :type="showOld ? 'text' : 'password'"
                placeholder="当前密码"
                maxlength="128"
                class="h-9 w-full rounded-lg border border-line-2 bg-surface pl-3 pr-9 text-[13px] text-ink outline-none transition placeholder:text-ink-faint/70 focus:border-accent focus:ring-2 focus:ring-accent/15"
              />
              <button
                type="button"
                class="absolute right-1 top-1/2 grid h-7 w-7 -translate-y-1/2 place-items-center rounded-md text-ink-faint transition hover:text-ink"
                :title="showOld ? '隐藏密码' : '显示密码'"
                @click="showOld = !showOld"
              >
                <Icon :name="showOld ? 'eyeOff' : 'eye'" :size="15" />
              </button>
            </div>
            <div class="relative">
              <input
                v-model="pwdForm.newPassword"
                :type="showNew ? 'text' : 'password'"
                placeholder="新密码（至少 6 位）"
                maxlength="128"
                class="h-9 w-full rounded-lg border border-line-2 bg-surface pl-3 pr-9 text-[13px] text-ink outline-none transition placeholder:text-ink-faint/70 focus:border-accent focus:ring-2 focus:ring-accent/15"
              />
              <button
                type="button"
                class="absolute right-1 top-1/2 grid h-7 w-7 -translate-y-1/2 place-items-center rounded-md text-ink-faint transition hover:text-ink"
                :title="showNew ? '隐藏密码' : '显示密码'"
                @click="showNew = !showNew"
              >
                <Icon :name="showNew ? 'eyeOff' : 'eye'" :size="15" />
              </button>
            </div>
          </div>
          <button
            :disabled="saving || !pwdForm.oldPassword || !pwdForm.newPassword"
            class="mt-3 h-9 w-full rounded-lg bg-accent text-[13px] font-medium text-white transition hover:brightness-110 active:scale-[0.99] disabled:cursor-not-allowed disabled:opacity-40"
            @click="savePassword"
          >
            {{ saving ? "保存中…" : "更新密码" }}
          </button>
        </div>
      </div>
      <!-- 数据与账号 -->
      <div class="overflow-hidden rounded-xl border border-line">
        <div class="flex items-center gap-2 border-b border-line bg-surface-2 px-4 py-2.5">
          <Icon name="db" :size="14" class="flex-shrink-0 text-accent" />
          <span class="text-[12.5px] font-medium">数据与账号</span>
        </div>
        <div class="flex flex-col gap-2.5 p-4">
          <button
            class="flex h-9 items-center justify-center gap-2 rounded-lg border border-line-2 text-[12.5px] text-ink-dim transition hover:border-accent/50 hover:text-ink"
            @click="exportData"
          >
            <Icon name="download" :size="13" />
            导出我的数据（JSON）
          </button>
          <button
            class="flex h-9 items-center justify-center gap-2 rounded-lg border border-err/30 text-[12.5px] text-err transition hover:bg-err/10"
            @click="deleteAccount"
          >
            <Icon name="trash" :size="13" />
            注销账号
          </button>
        </div>
      </div>
      <!-- 反馈条 -->
      <div
        v-if="formMsg"
        class="flex items-center gap-2 rounded-xl border border-line px-4 py-2.5 text-[12.5px] transition"
        :class="formOk ? 'border-ok/25 bg-ok/8 text-ok' : 'border-err/25 bg-err/8 text-err'"
      >
        <Icon :name="formOk ? 'check' : 'warn'" :size="14" class="flex-shrink-0" />
        {{ formMsg }}
      </div>
    </div>
  </Modal>
</template>
