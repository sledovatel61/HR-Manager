/**
 * Минимальные объявления File System Access API.
 *
 * ``window.showSaveFilePicker`` — системный диалог «Сохранить как…» — есть в
 * браузерах на Chromium и отсутствует в стандартном ``lib.dom.d.ts``
 * TypeScript. Объявляем только то, что реально используется в
 * ``saveCandidateAttachmentWithPicker``; остальное (``FileSystemFileHandle``,
 * ``FileSystemWritableFileStream``) уже есть в ``lib.dom``.
 *
 * API доступен только в безопасном контексте (https или localhost), поэтому
 * наличие метода проверяется в рантайме — см. ``saveDialogAvailable()``.
 */

interface SaveFilePickerAcceptType {
  description?: string;
  accept: Record<string, string[]>;
}

interface SaveFilePickerOptions {
  suggestedName?: string;
  types?: SaveFilePickerAcceptType[];
  excludeAcceptAllOption?: boolean;
  id?: string;
  startIn?: string;
}

interface Window {
  showSaveFilePicker?: (options?: SaveFilePickerOptions) => Promise<FileSystemFileHandle>;
}
