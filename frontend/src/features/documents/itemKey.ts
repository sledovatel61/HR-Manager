/**
 * Технический ключ документа в списке (block D).
 *
 * Сервер требует, чтобы ключи внутри версии были уникальными, и по ним
 * хранит состояние документов у кандидатов. Обычному HR этот ключ не нужен,
 * поэтому он выводится из названия автоматически и показывается только в
 * «Расширенных сведениях».
 */

/** True when every code point is below 128 (plain ASCII key material). */
const isAscii = (value: string) => Array.from(value).every((c) => (c.codePointAt(0) ?? 0) < 128);

/**
 * Ключ из русского названия: транслитерация не выполняется — ключ
 * технический, а сервер принимает любую уникальную строку. Совпадения
 * разрешаются суффиксом `_2`, `_3`, …
 */
export function autoKey(name: string, taken: Iterable<string>): string {
  const used = new Set(taken);
  const cleaned = name
    .toLowerCase()
    .replace(/[^a-zа-яё0-9]+/gi, "_")
    .replace(/^_+|_+$/g, "")
    .slice(0, 40);
  // Название целиком на русском не даёт ASCII-ключа: используем нейтральный
  // префикс с длиной, чтобы ключ оставался читаемым и уникальным.
  const base = cleaned === "" ? "document" : isAscii(cleaned) ? cleaned : `doc_${cleaned.length}`;
  if (!used.has(base)) return base;
  let counter = 2;
  while (used.has(`${base}_${counter}`)) counter += 1;
  return `${base}_${counter}`;
}
