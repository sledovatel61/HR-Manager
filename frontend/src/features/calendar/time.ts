/** Local-time helpers for calendar inputs.

 * The API exchanges UTC ISO 8601 timestamps; `<input type="datetime-local">`
 * works in the browser's local timezone. These helpers convert predictably
 * in both directions.
 */

/** ISO 8601 (UTC) → value for <input type="datetime-local"> (browser local). */
export function toLocalInput(iso: string | null): string {
  if (!iso) return "";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  const pad = (value: number) => String(value).padStart(2, "0");
  return (
    `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}` +
    `T${pad(date.getHours())}:${pad(date.getMinutes())}`
  );
}

/** <input type="datetime-local"> value (browser local) → ISO 8601 UTC. */
export function fromLocalInput(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return date.toISOString();
}

/** Monday 00:00 (local) of the week containing `date`. */
export function startOfWeek(date: Date): Date {
  const result = new Date(date);
  const day = (result.getDay() + 6) % 7; // Monday = 0
  result.setDate(result.getDate() - day);
  result.setHours(0, 0, 0, 0);
  return result;
}

/** Short local time "14:05". */
export function formatTime(iso: string | null): string {
  if (!iso) return "";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
}

/** "пн, 03.09" style day label. */
export function formatDayLabel(date: Date): string {
  const weekday = date.toLocaleDateString("ru-RU", { weekday: "short" });
  const day = date.toLocaleDateString("ru-RU", { day: "2-digit", month: "2-digit" });
  return `${weekday}, ${day}`;
}

/** "3–9 сентября" style range label. */
export function formatWeekRange(start: Date): string {
  const end = new Date(start);
  end.setDate(end.getDate() + 4);
  const month = start.toLocaleDateString("ru-RU", { month: "long" });
  if (start.getMonth() === end.getMonth()) {
    return `${start.getDate()}–${end.getDate()} ${month}`;
  }
  return (
    `${start.getDate()} ${start.toLocaleDateString("ru-RU", { month: "short" })}` +
    ` — ${end.getDate()} ${end.toLocaleDateString("ru-RU", { month: "short" })}`
  );
}
