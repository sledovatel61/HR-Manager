/**
 * Vocabulary of the methodical base (UX feedback 2026-09-29).
 *
 * Kept out of the page component so the file exports only components: the
 * helpers are pure and are unit-tested directly.
 */
import type { TemplatePlaceholder } from "../../types";

/**
 * Template kinds stay a controlled key list — a free-text kind would break
 * reports. The labels are the words an HR actually uses, while the stored
 * keys keep their original meaning, so nothing already in the database
 * re-renders under a different name.
 */
export interface KindOption {
  value: string;
  label: string;
}

export const KIND_OPTIONS: readonly KindOption[] = [
  { value: "document", label: "Документ" },
  { value: "checklist", label: "Чек-лист" },
  { value: "interview", label: "Вопросник для интервью" },
  { value: "memo", label: "Памятка HR" },
  { value: "script", label: "Скрипт" },
  { value: "offer", label: "Оффер" },
  { value: "anketa", label: "Анкета" },
  { value: "dogovor", label: "Договор" },
  { value: "form", label: "Форма" },
];

export function kindLabel(kind: string): string {
  return KIND_OPTIONS.find((option) => option.value === kind)?.label ?? kind;
}

/**
 * A legal-form template must be re-checked by a human: the list cannot prove
 * a document is current, so the UI says so instead of letting a stale offer
 * look approved.
 */
export const REVIEW_KINDS: ReadonlySet<string> = new Set(["offer", "dogovor"]);

export const TEMPLATE_IMPORT_EXTENSIONS = [".txt", ".md", ".markdown", ".csv"];
export const TEMPLATE_IMPORT_MAX_BYTES = 512 * 1024;

/**
 * Buttons in the editor show the field a person recognises, not the token the
 * server stores. The token stays in the tooltip and in «Как это выглядит в
 * тексте шаблона».
 */
export function friendlyPlaceholderName(item: TemplatePlaceholder): string {
  const description = item.description.trim();
  if (description) return description;
  return item.token.split(".").at(-1) ?? item.token;
}

/**
 * Client-side pre-check for the import form. The server re-checks everything
 * regardless — this only saves a pointless round trip and gives a fast,
 * specific message.
 */
export function describeImportFile(file: File | null): string | null {
  if (!file) return "Выберите файл с материалом.";
  const name = file.name.toLowerCase();
  if (!TEMPLATE_IMPORT_EXTENSIONS.some((extension) => name.endsWith(extension))) {
    return `Формат не поддерживается. Выберите файл: ${TEMPLATE_IMPORT_EXTENSIONS.join(", ")}.`;
  }
  if (file.size > TEMPLATE_IMPORT_MAX_BYTES) {
    return `Файл больше ${TEMPLATE_IMPORT_MAX_BYTES / 1024} КБ — сократите материал.`;
  }
  if (file.size === 0) return "Файл пуст.";
  return null;
}
