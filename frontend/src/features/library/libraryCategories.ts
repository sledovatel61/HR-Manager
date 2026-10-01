/**
 * Vocabulary of the «Библиотека HR» screen.
 *
 * Kept out of the components so the helpers stay pure and unit-testable.
 * The category keys are the closed dictionary stored on the server
 * (``app.library.LIBRARY_CATEGORIES``); this module only pins the display
 * order and the Russian labels used when a card arrives without the server
 * catalog.
 */
import type { LibraryCategory } from "../../types";

export interface LibraryCategoryOption {
  value: string;
  label: string;
}

/** Display order of the filter chips; «Все» is rendered separately. */
export const LIBRARY_CATEGORY_ORDER: readonly LibraryCategoryOption[] = [
  { value: "interview", label: "Собеседование" },
  { value: "candidate_docs", label: "Документы кандидата" },
  { value: "calls", label: "Звонки и сообщения" },
  { value: "onboarding", label: "Онбординг" },
  { value: "memos", label: "Памятки HR" },
  { value: "position", label: "Вопросники по должностям" },
];

export function libraryCategoryLabel(key: string): string {
  return (
    LIBRARY_CATEGORY_ORDER.find((option) => option.value === key)?.label ??
    (key ? key : "Без категории")
  );
}

/**
 * Server categories first (they are the authoritative closed dictionary),
 * client order and labels as the fallback for resilience.
 */
export function mergeCategories(
  server: readonly LibraryCategory[]
): LibraryCategoryOption[] {
  if (server.length === 0) return [...LIBRARY_CATEGORY_ORDER];
  const order = new Map(LIBRARY_CATEGORY_ORDER.map((option, index) => [option.value, index]));
  return [...server]
    .map((category, index) => ({
      value: category.key,
      label: category.label || libraryCategoryLabel(category.key),
      sort: order.get(category.key) ?? LIBRARY_CATEGORY_ORDER.length + index,
    }))
    .sort((a, b) => a.sort - b.sort)
    .map(({ value, label }) => ({ value, label }));
}

/**
 * Kind label for a card. The kinds are the controlled template list
 * (`document-templates/vocabulary`), repeated labels for «производственные
 * вопросники» stay the same words the manage screen uses.
 */
export { kindLabel as libraryKindLabel } from "../document-templates/vocabulary";

/**
 * Count of materials per category key (chips may show a counter).
 */
export function countByCategory<T extends { category: string }>(
  items: readonly T[]
): Map<string, number> {
  const counts = new Map<string, number>();
  for (const item of items) {
    counts.set(item.category, (counts.get(item.category) ?? 0) + 1);
  }
  return counts;
}
