import "./avatar.css";

const COLOR_MAP: Record<string, string> = {
  violet: "var(--status-violet-fg)",
  teal: "var(--status-teal-fg)",
  amber: "var(--status-warning-fg)",
  indigo: "var(--status-indigo-fg)",
  rose: "var(--palette-rose-500)",
  slate: "var(--text-secondary)",
  cyan: "var(--palette-cyan-500)",
};

const BG_MAP: Record<string, string> = {
  violet: "var(--status-violet-bg)",
  teal: "var(--status-teal-bg)",
  amber: "var(--status-warning-bg)",
  indigo: "var(--status-indigo-bg)",
  rose: "var(--palette-rose-50)",
  slate: "var(--status-neutral-bg)",
  cyan: "var(--palette-cyan-50)",
};

interface AvatarProps {
  initials: string;
  color?: string;
  size?: "sm" | "md" | "lg";
  name?: string;
}

/**
 * Аватар из инициалов (никаких внешних изображений/CDN — см. ограничения
 * промпта). `name`, если задан, делает аватар доступным для screen reader;
 * иначе аватар считается декоративным (используется рядом с видимым именем).
 */
export function Avatar({ initials, color = "slate", size = "md", name }: AvatarProps) {
  return (
    <span
      className={`avatar avatar-${size}`}
      style={{ color: COLOR_MAP[color] ?? COLOR_MAP.slate, background: BG_MAP[color] ?? BG_MAP.slate }}
      role={name ? "img" : undefined}
      aria-label={name}
      aria-hidden={name ? undefined : true}
    >
      {initials}
    </span>
  );
}
