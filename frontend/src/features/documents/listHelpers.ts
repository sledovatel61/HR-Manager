/** Pure helpers of the admin document lists screen (phase 11): item draft
 * validation and key generation. Kept apart from the component for fast
 * refresh and for unit tests without rendering. */

import type { DocumentListItemInput } from "../../types";

export const MAX_ITEMS = 20;
const ITEM_KEY_PATTERN = /^[a-z0-9][a-z0-9_-]{0,63}$/;

export interface ItemDraft {
  item_key: string;
  name: string;
  explanation: string;
  is_required: boolean;
}

export const EMPTY_ITEM: ItemDraft = { item_key: "", name: "", explanation: "", is_required: true };

export function slugify(name: string): string {
  const translit: Record<string, string> = {
    а: "a", б: "b", в: "v", г: "g", д: "d", е: "e", ё: "e", ж: "zh", з: "z", и: "i", й: "y",
    к: "k", л: "l", м: "m", н: "n", о: "o", п: "p", р: "r", с: "s", т: "t", у: "u", ф: "f",
    х: "h", ц: "c", ч: "ch", ш: "sh", щ: "sch", ъ: "", ы: "y", ь: "", э: "e", ю: "yu", я: "ya",
  };
  const ascii = name
    .toLowerCase()
    .split("")
    .map((char) => translit[char] ?? char)
    .join("")
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 64);
  return ascii.replace(/^[^a-z0-9]+/, "");
}

/** Validate item drafts; returns the payload or a Russian message. */
export function buildItems(
  drafts: ItemDraft[],
): { items: DocumentListItemInput[] } | { error: string } {
  if (drafts.length > MAX_ITEMS) {
    return { error: `Не больше ${MAX_ITEMS} элементов в списке.` };
  }
  const keys = new Set<string>();
  const items: DocumentListItemInput[] = [];
  for (const [index, draft] of drafts.entries()) {
    const name = draft.name.trim();
    const key = draft.item_key.trim() || slugify(name);
    if (!name) {
      return { error: `Элемент ${index + 1}: укажите название документа.` };
    }
    if (!ITEM_KEY_PATTERN.test(key)) {
      return {
        error: `Элемент ${index + 1}: ключ — латинские буквы в нижнем регистре, цифры, «-» и «_».`,
      };
    }
    if (keys.has(key)) {
      return { error: `Элемент ${index + 1}: ключ «${key}» уже используется.` };
    }
    keys.add(key);
    items.push({
      item_key: key,
      name,
      explanation: draft.explanation.trim(),
      is_required: draft.is_required,
    });
  }
  return { items };
}
