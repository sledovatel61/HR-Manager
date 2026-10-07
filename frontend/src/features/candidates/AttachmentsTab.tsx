import { useCallback, useMemo, useRef, useState } from "react";
import {
  ApiError,
  deleteCandidateAttachment,
  fetchCandidateAttachment,
  listCandidateAttachments,
  openSavedFile,
  saveCandidateAttachmentWithPicker,
  saveDialogAvailable,
  triggerBrowserDownload,
  uploadCandidateAttachment,
} from "../../api";
import type { CandidateAttachment } from "../../types";
import { Button, IconButton } from "../../design-system/components/Button";
import { ConfirmDialog } from "../../design-system/components/ConfirmDialog";
import { LoadState } from "../documents/shared";
import { errorText, useResource } from "../documents/hooks";
import { formatDateTime } from "./format";
import "./attachments.css";

/** Разрешённые форматы — те же, что проверяет сервер по сигнатуре. */
const ACCEPT = ".docx,.pdf";
const KIND_LABELS: Record<string, string> = {
  docx: "Документ Word",
  pdf: "Скан (PDF)",
};

const BROWSER_LIMIT_NOTE =
  "Браузер не может открыть файл в Word или Acrobat. Чтобы открыть документ в " +
  "нужной программе, воспользуйтесь списком загрузок браузера (Ctrl+J) → " +
  "«Открыть» или откройте файл из папки «Загрузки».";

function formatBytes(value: number): string {
  if (value >= 1024 * 1024) return `${(value / (1024 * 1024)).toFixed(1)} МБ`;
  if (value >= 1024) return `${Math.round(value / 1024)} КБ`;
  return `${value} Б`;
}

interface UploadState {
  filename: string;
  percent: number;
}

export function AttachmentsTab({ candidateId, onChanged }: { candidateId: string; onChanged?: () => void }) {
  const load = useCallback(() => listCandidateAttachments(candidateId), [candidateId]);
  const resource = useResource(load);
  const uploadBusy = useRef(false);
  const fileInput = useRef<HTMLInputElement | null>(null);
  const [uploading, setUploading] = useState<UploadState | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [pendingDelete, setPendingDelete] = useState<CandidateAttachment | null>(null);
  /** Дескрипторы реально сохранённых файлов: только они позволяют открыть байты. */
  const [savedHandles, setSavedHandles] = useState<Record<string, FileSystemFileHandle>>({});

  const data = resource.data;
  const canManage = data?.can_manage ?? false;
  const pickerAvailable = useMemo(() => saveDialogAvailable(), []);

  const limitsReached =
    !!data &&
    (data.total >= data.limits.max_count || data.total_bytes >= data.limits.max_total_bytes);

  const resetMessages = () => {
    setError("");
    setNotice("");
  };

  const pickFile = () => {
    resetMessages();
    fileInput.current?.click();
  };

  const onFileChosen = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    // Значение сбрасываем сразу, чтобы повторный выбор того же файла сработал.
    event.target.value = "";
    if (!file || !data || !canManage || limitsReached || uploadBusy.current) return;
    resetMessages();

    const name = file.name.toLowerCase();
    if (!name.endsWith(".docx") && !name.endsWith(".pdf")) {
      setError("Допустимые форматы: .docx и .pdf. Исполняемые файлы и архивы не принимаются.");
      return;
    }
    if (file.size > data.limits.max_file_bytes) {
      setError(
        `Файл больше допустимого размера ${formatBytes(data.limits.max_file_bytes)}. ` +
          "Сократите файл или сохраните скан с меньшим разрешением.",
      );
      return;
    }
    if (data.total_bytes + file.size > data.limits.max_total_bytes) {
      setError(
        `Не хватает места: на кандидата приходится не больше ` +
          `${formatBytes(data.limits.max_total_bytes)} вложений.`,
      );
      return;
    }

    uploadBusy.current = true;
    setUploading({ filename: file.name, percent: 0 });
    try {
      const created = await uploadCandidateAttachment(candidateId, file, (percent) =>
        setUploading({ filename: file.name, percent }),
      );
      onChanged?.();
      await resource.reload();
      setNotice(`Файл «${created.filename}» загружен.`);
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : errorText(caught));
    } finally {
      uploadBusy.current = false;
      setUploading(null);
    }
  };

  const download = async (attachment: CandidateAttachment) => {
    resetMessages();
    setBusyId(attachment.id);
    try {
      const { blob, filename } = await fetchCandidateAttachment(candidateId, attachment.id);
      triggerBrowserDownload(blob, filename);
      setNotice(
        `Файл «${filename}» передан в загрузки браузера. ${BROWSER_LIMIT_NOTE}`,
      );
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : errorText(caught));
    } finally {
      setBusyId(null);
    }
  };

  const saveAs = async (attachment: CandidateAttachment) => {
    resetMessages();
    setBusyId(attachment.id);
    try {
      const result = await saveCandidateAttachmentWithPicker(candidateId, attachment.id, attachment.filename);
      if (result.outcome === "cancelled") {
        // Отмена диалога — не успех: ничего не сохранено и сообщение нейтральное.
        setNotice("Сохранение отменено.");
        return;
      }
      if (result.handle) {
        setSavedHandles((current) => ({ ...current, [attachment.id]: result.handle! }));
      }
      setNotice(`Файл «${result.filename}» сохранён.`);
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : errorText(caught));
    } finally {
      setBusyId(null);
    }
  };

  const openSaved = async (attachment: CandidateAttachment) => {
    const handle = savedHandles[attachment.id];
    if (!handle) return;
    resetMessages();
    setBusyId(attachment.id);
    try {
      await openSavedFile(handle);
      setNotice(`Сохранённый файл передан браузеру. ${BROWSER_LIMIT_NOTE}`);
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : errorText(caught));
    } finally {
      setBusyId(null);
    }
  };

  const confirmDelete = async () => {
    const attachment = pendingDelete;
    if (!attachment) return;
    setPendingDelete(null);
    resetMessages();
    setBusyId(attachment.id);
    try {
      await deleteCandidateAttachment(candidateId, attachment.id);
      onChanged?.();
      await resource.reload();
      setNotice(`Файл «${attachment.filename}» удалён из карточки.`);
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : errorText(caught));
    } finally {
      setBusyId(null);
    }
  };

  return (
    <section className="attachments-panel">
      <h3>Документы и анкеты</h3>
      <p className="attachments-hint">
        Принимаются анкеты .docx и сканы .pdf. Сервер проверяет тип файла по
        содержимому, а не по имени. До {data ? formatBytes(data.limits.max_file_bytes) : "10 МБ"} на
        файл{data ? `, не больше ${data.limits.max_count} файлов на кандидата` : ""}.
      </p>

      <LoadState {...resource} />

      {data && (
        <>
          <div className="attachments-toolbar">
            <input
              ref={fileInput}
              type="file"
              accept={ACCEPT}
              className="sr-only"
              aria-label="Файл анкеты или скана"
              disabled={!canManage || limitsReached || uploading !== null}
              onChange={(event) => void onFileChosen(event)}
            />
            <Button
              variant="primary"
              size="sm"
              icon="plus"
              onClick={pickFile}
              disabled={!canManage || limitsReached || uploading !== null}
              title={
                !canManage
                  ? "Недостаточно прав для загрузки вложений этого кандидата"
                  : limitsReached
                    ? "Достигнут предел числа или объёма вложений"
                    : undefined
              }
            >
              Загрузить анкету
            </Button>
            {canManage ? (
              <span className="attachments-quota">
                {data.total} из {data.limits.max_count} · {formatBytes(data.total_bytes)} из{" "}
                {formatBytes(data.limits.max_total_bytes)}
              </span>
            ) : (
              <span className="attachments-quota">
                Загрузка и удаление доступны ответственному за кандидата
              </span>
            )}
          </div>

          {uploading && (
            <p role="status" className="attachments-progress">
              Загрузка «{uploading.filename}»… {uploading.percent}%
              <progress aria-label="Загрузка файла" max={100} value={uploading.percent} />
            </p>
          )}

          {notice && (
            <p role="status" className="attachments-notice">
              {notice}
            </p>
          )}
          {error && (
            <p role="alert" className="attachments-error">
              {error}
            </p>
          )}

          {data.items.length === 0 ? (
            <p className="attachments-empty">Вложений пока нет.</p>
          ) : (
            <ul className="attachments-list">
              {data.items.map((attachment) => (
                <li key={attachment.id} className="attachments-item">
                  <div className="attachments-item-main">
                    <span className="attachments-name" title={attachment.filename}>
                      {attachment.filename}
                    </span>
                    <span className="attachments-meta">
                      {KIND_LABELS[attachment.kind] ?? attachment.kind} ·{" "}
                      {formatBytes(attachment.size_bytes)} ·{" "}
                      {formatDateTime(attachment.uploaded_at)} ·{" "}
                      {attachment.uploaded_by_username || "неизвестно"}
                    </span>
                  </div>
                  <div className="attachments-actions">
                    <Button
                      variant="secondary"
                      size="sm"
                      onClick={() => void download(attachment)}
                      disabled={busyId === attachment.id}
                    >
                      Скачать
                    </Button>
                    {pickerAvailable && (
                      <Button
                        variant="secondary"
                        size="sm"
                        onClick={() => void saveAs(attachment)}
                        disabled={busyId === attachment.id}
                      >
                        Сохранить как…
                      </Button>
                    )}
                    {savedHandles[attachment.id] && (
                      <Button
                        variant="secondary"
                        size="sm"
                        onClick={() => void openSaved(attachment)}
                        disabled={busyId === attachment.id}
                      >
                        Открыть скачанный файл
                      </Button>
                    )}
                    {canManage && (
                      <IconButton
                        icon="trash"
                        label={`Удалить ${attachment.filename}`}
                        size="sm"
                        onClick={() => setPendingDelete(attachment)}
                        disabled={busyId === attachment.id}
                      />
                    )}
                  </div>
                </li>
              ))}
            </ul>
          )}

          <p className="attachments-note">{BROWSER_LIMIT_NOTE}</p>
          {!pickerAvailable && (
            <p className="attachments-note">
              Диалог «Сохранить как…» в этом браузере недоступен (нужен https
              или localhost), поэтому файл сохраняется через загрузки браузера.
            </p>
          )}
        </>
      )}

      <ConfirmDialog
        open={pendingDelete !== null}
        onCancel={() => setPendingDelete(null)}
        onConfirm={() => void confirmDelete()}
        title="Удалить вложение?"
        description={
          pendingDelete
            ? `Файл «${pendingDelete.filename}» исчезнет из карточки кандидата. Действие записывается в журнал аудита.`
            : ""
        }
        confirmLabel="Удалить"
        danger
      />
    </section>
  );
}
