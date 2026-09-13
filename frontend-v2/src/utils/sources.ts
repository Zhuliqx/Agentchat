import type { SourceRef } from "@/types/api";

/**
 * 兼容两种引用溯源数据：
 * - 新记录：[{ path, hits }]（hits = 该来源命中的检索片段数）
 * - 老记录：["path", ...]（没有命中数）
 */
export function normalizeSources(raw: unknown): SourceRef[] {
  if (!Array.isArray(raw)) return [];
  const out: SourceRef[] = [];
  for (const item of raw) {
    if (typeof item === "string") {
      if (item) out.push({ path: item });
      continue;
    }
    if (item && typeof item === "object") {
      const { path, hits } = item as { path?: unknown; hits?: unknown };
      if (typeof path === "string" && path) {
        out.push({ path, hits: typeof hits === "number" && hits > 0 ? hits : undefined });
      }
    }
  }
  return out;
}

/** 只取文件名：Windows 路径用反斜杠，需同时按两种分隔符切分 */
export function sourceName(path: string): string {
  return path.split(/[\\/]/).pop() || path;
}

/** chip 悬停提示：带命中片段数时一并说明 */
export function sourceTitle(ref: SourceRef): string {
  return ref.hits && ref.hits > 1 ? `${ref.path}（命中 ${ref.hits} 段）` : ref.path;
}
