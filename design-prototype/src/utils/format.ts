export function formatDate(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleDateString("ru-RU", { day: "2-digit", month: "short", year: "numeric" });
}

export function formatDateTime(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleString("ru-RU", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });
}

export function formatRelative(iso: string, from: Date): string {
  const target = new Date(iso);
  const diffMs = target.getTime() - from.getTime();
  const diffDays = Math.round(diffMs / (1000 * 60 * 60 * 24));
  if (diffDays === 0) return "сегодня";
  if (diffDays === 1) return "завтра";
  if (diffDays === -1) return "вчера";
  if (diffDays > 1) return `через ${diffDays} дн.`;
  return `${Math.abs(diffDays)} дн. назад`;
}
