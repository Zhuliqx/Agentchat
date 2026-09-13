/**
 * 相对时间文案：刚刚 / N 分钟前 / N 小时前 / 昨天 / M-D（跨年带年份）。
 * 会话头部的"最后更新"用它显示，比绝对时间戳更容易判断新旧。
 */
export function relativeTime(iso: string | undefined | null, nowMs = Date.now()): string {
  if (!iso) return "";
  const t = new Date(iso).getTime();
  if (!Number.isFinite(t)) return "";

  const diff = nowMs - t;
  if (diff < 0) return "刚刚"; // 时钟偏差按"刚刚"处理
  if (diff < 60_000) return "刚刚";
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)} 分钟前`;
  if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)} 小时前`;
  if (diff < 172_800_000) return "昨天";

  const d = new Date(t);
  const sameYear = new Date(nowMs).getFullYear() === d.getFullYear();
  const md = `${d.getMonth() + 1}-${d.getDate()}`;
  return sameYear ? md : `${d.getFullYear()}-${md}`;
}

/** 绝对时间（悬停提示用）：2026-09-13 16:20 */
export function absoluteTime(iso: string | undefined | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}
