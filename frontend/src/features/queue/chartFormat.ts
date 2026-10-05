/**
 * Форматирование чисел и шкала для графиков «Моя очередь».
 *
 * Отдельный файл, потому что `QueueCharts.tsx` экспортирует только
 * компоненты: иначе правило react-refresh/only-export-components ругается и
 * быстрая перезагрузка перестаёт работать.
 */

const intFmt = new Intl.NumberFormat("ru-RU");

export function formatInt(value: number | null | undefined): string {
  if (value === null || value === undefined) return "\u2014";
  return intFmt.format(value);
}

export function formatPercent(value: number | null | undefined): string {
  if (value === null || value === undefined) return "\u2014";
  // Неразрывный пробел перед % (типографическое правило) задаётся
  // escape-последовательностью: литеральный NBSP в исходнике запрещён
  // правилом no-irregular-whitespace.
  return `${value.toLocaleString("ru-RU", { maximumFractionDigits: 1 })}\u00a0%`;
}

export function formatDays(value: number | null | undefined): string {
  if (value === null || value === undefined) return "\u2014";
  return `${value.toLocaleString("ru-RU", { maximumFractionDigits: 1 })} дн.`;
}

export interface Scale {
  min: number;
  max: number;
  step: number;
}

/** «Круглая» шкала: подписи оси попадают на понятные значения (0, 5, 10…). */
export function niceScale(min: number, max: number, tickCount = 4): Scale {
  const safeMin = Number.isFinite(min) ? Math.min(0, min) : 0;
  let safeMax = Number.isFinite(max) ? max : 1;
  if (safeMax <= safeMin) safeMax = safeMin + 1;
  const rawStep = (safeMax - safeMin) / tickCount;
  const magnitude = 10 ** Math.floor(Math.log10(rawStep || 1));
  const norm = rawStep / magnitude;
  const step = (norm <= 1 ? 1 : norm <= 2 ? 2 : norm <= 5 ? 5 : 10) * magnitude;
  return {
    min: Math.floor(safeMin / step) * step,
    max: Math.ceil(safeMax / step) * step,
    step,
  };
}
