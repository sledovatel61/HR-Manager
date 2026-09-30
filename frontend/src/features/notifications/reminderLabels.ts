import type { ReminderStatus } from "../../types";

/**
 * Russian status labels for a personal reminder. Shared so the candidate card
 * and «Напоминания» cannot drift apart in wording.
 */
export const REMINDER_STATUS_LABELS: Record<ReminderStatus, string> = {
  active: "Активное",
  completed: "Выполнено",
  cancelled: "Отменено",
};
