// 头像色板：color key → Tailwind 颜色类与中文标签
// key 与后端 User.avatar_color 对应；任何地方显示首字母头像都用这里映射。
export interface AvatarColorDef {
  dot: string; // 纯色圆点（色板选择器用）
  bg: string; // 头像底色（半透明）
  text: string; // 头像文字色
  label: string;
}

export const AVATAR_COLORS: Record<string, AvatarColorDef> = {
  accent: { dot: "bg-accent", bg: "bg-accent/18", text: "text-accent", label: "蓝" },
  ok: { dot: "bg-ok", bg: "bg-ok/18", text: "text-ok", label: "绿" },
  warn: { dot: "bg-warn", bg: "bg-warn/18", text: "text-warn", label: "琥珀" },
  err: { dot: "bg-err", bg: "bg-err/18", text: "text-err", label: "红" },
  orbit: { dot: "bg-orbit", bg: "bg-orbit/18", text: "text-orbit", label: "青" },
};

export const AVATAR_ORDER = Object.keys(AVATAR_COLORS);

/** 按用户 avatar_color 取色板定义（未知 key 回退 accent）。 */
export function avatarColor(user?: { avatar_color?: string } | null): AvatarColorDef {
  const key = user?.avatar_color || "accent";
  return AVATAR_COLORS[key] || AVATAR_COLORS.accent;
}
