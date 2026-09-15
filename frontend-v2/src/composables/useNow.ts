import { onBeforeUnmount, ref } from "vue";

/**
 * 定时刷新的"当前时间"：相对时间文案（刚刚 / N 分钟前）需要它才会自己往下走，
 * 否则页面长时间停留会一直显示旧文案。
 */
export function useNow(intervalMs = 60_000) {
  const now = ref(Date.now());
  const timer = setInterval(() => (now.value = Date.now()), intervalMs);
  onBeforeUnmount(() => clearInterval(timer));
  return now;
}
