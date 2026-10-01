import { useMemo, useState } from "react";
import {
  ApiError,
  SCHEDULE_IMPORT_MAX_BYTES,
  confirmWorkScheduleImport,
  previewWorkScheduleImport,
} from "../../api";
import { Button } from "../../design-system/components/Button";
import { Modal } from "../../design-system/components/Modal";
import { Badge } from "../../design-system/components/StatusChip";
import { useToast } from "../../design-system/components/ToastContext";
import type {
  ImportDecisionAction,
  ImportRowDecision,
  ImportRowPreview,
  WorkScheduleImportPreview,
  WorkScheduleImportResult,
} from "../../types";

interface ScheduleImportDialogProps {
  onClose: () => void;
  /** Импорт подтверждён — страница перечитывает график. */
  onImported: () => void;
}

interface RowDecisionOverride {
  action: ImportDecisionAction;
  candidate_id: string | null;
}

/** Действие по строке с учётом выбора пользователя (иначе — предложение сервера). */
function effectiveDecision(
  row: ImportRowPreview,
  overrides: Record<number, RowDecisionOverride>
): RowDecisionOverride {
  const override = overrides[row.row_index];
  if (override) return override;
  if (row.suggested_action === "match") {
    return { action: "match", candidate_id: row.match?.candidate_id ?? null };
  }
  return { action: row.suggested_action, candidate_id: null };
}

function formatDay(iso: string): string {
  const [year, month, day] = iso.split("-");
  return `${day}.${month}.${year}`;
}

/** Строка требует внимания: выбор кандидата, предупреждения или ошибка разбора. */
function needsAttention(row: ImportRowPreview): boolean {
  if (row.parse_error) return true;
  if (row.match_options.length > 0 && !row.match) return true;
  if (row.warnings.length > 0) return true;
  if (row.match && !row.match.confident) return true;
  return false;
}

const ACTION_LABELS: Record<ImportDecisionAction, string> = {
  create: "Создать кандидата",
  match: "Сопоставить",
  service: "Служебная запись",
  skip: "Пропустить",
};

/**
 * Импорт графика выхода из Excel: выбор файла → предпросмотр (проверка без
 * сохранения) → явное подтверждение. Сервер не хранит файл; действия по
 * неоднозначным строкам выбирает пользователь (пропустить / сопоставить /
 * создать / служебная запись).
 */
export function ScheduleImportDialog({ onClose, onImported }: ScheduleImportDialogProps) {
  const { pushToast } = useToast();

  const [fileName, setFileName] = useState<string | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<WorkScheduleImportPreview | null>(null);
  const [previewing, setPreviewing] = useState(false);
  const [previewError, setPreviewError] = useState<string | null>(null);

  const [overrides, setOverrides] = useState<Record<number, RowDecisionOverride>>({});
  const [onlyAttention, setOnlyAttention] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [confirmError, setConfirmError] = useState<string | null>(null);
  const [result, setResult] = useState<WorkScheduleImportResult | null>(null);

  const handleFileChange = async (nextFile: File | null) => {
    setPreview(null);
    setPreviewError(null);
    setOverrides({});
    setResult(null);
    setConfirmError(null);
    if (!nextFile) {
      setFile(null);
      setFileName(null);
      return;
    }
    if (!nextFile.name.toLowerCase().endsWith(".xlsx")) {
      setFile(null);
      setFileName(nextFile.name);
      setPreviewError("Нужен файл графика в формате .xlsx (без макросов).");
      return;
    }
    if (nextFile.size > SCHEDULE_IMPORT_MAX_BYTES) {
      setFile(null);
      setFileName(nextFile.name);
      setPreviewError(
        `Файл больше ${SCHEDULE_IMPORT_MAX_BYTES / (1024 * 1024)} МБ — сократите его.`
      );
      return;
    }
    setFile(nextFile);
    setFileName(nextFile.name);
    setPreviewing(true);
    try {
      setPreview(await previewWorkScheduleImport(nextFile));
    } catch (caught) {
      setPreview(null);
      setPreviewError(
        caught instanceof ApiError ? caught.message : "Не удалось разобрать файл."
      );
    } finally {
      setPreviewing(false);
    }
  };

  const decisions = useMemo(() => {
    if (!preview) return [];
    return preview.rows.map((row) => {
      const decision = effectiveDecision(row, overrides);
      return {
        row_index: row.row_index,
        action: decision.action,
        candidate_id: decision.candidate_id,
      } satisfies ImportRowDecision;
    });
  }, [preview, overrides]);

  const importableCount = useMemo(
    () => decisions.filter((decision) => decision.action !== "skip").length,
    [decisions]
  );

  const unresolvedMatches = useMemo(() => {
    if (!preview) return 0;
    return preview.rows.filter((row) => {
      const decision = effectiveDecision(row, overrides);
      return decision.action === "match" && !decision.candidate_id;
    }).length;
  }, [preview, overrides]);

  const setRowAction = (row: ImportRowPreview, action: ImportDecisionAction) => {
    const base = effectiveDecision(row, overrides);
    const candidate_id = action === "match" ? (base.candidate_id ?? row.match?.candidate_id ?? null) : null;
    setOverrides((current) => ({
      ...current,
      [row.row_index]: { action, candidate_id },
    }));
  };

  const setRowCandidate = (row: ImportRowPreview, candidateId: string) => {
    setOverrides((current) => ({
      ...current,
      [row.row_index]: { action: "match", candidate_id: candidateId || null },
    }));
  };

  const confirm = async () => {
    if (!file || confirming || unresolvedMatches > 0) return;
    setConfirming(true);
    setConfirmError(null);
    try {
      const imported = await confirmWorkScheduleImport(file, decisions);
      setResult(imported);
      pushToast("success", "Импорт графика завершён.");
    } catch (caught) {
      setConfirmError(
        caught instanceof ApiError ? caught.message : "Не удалось завершить импорт."
      );
    } finally {
      setConfirming(false);
    }
  };

  const downloadReport = () => {
    if (!result) return;
    const blob = new Blob([result.report_csv], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `import-report-${result.import_id.slice(0, 8)}.csv`;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    URL.revokeObjectURL(url);
  };

  const copyReport = async () => {
    if (!result) return;
    try {
      await navigator.clipboard.writeText(result.report_csv);
      pushToast("success", "Отчёт скопирован в буфер обмена.");
    } catch {
      pushToast("danger", "Не удалось скопировать отчёт — скачайте CSV.");
    }
  };

  const finish = () => {
    onImported();
    onClose();
  };

  const visibleRows = useMemo(() => {
    if (!preview) return [];
    if (!onlyAttention) return preview.rows;
    return preview.rows.filter(needsAttention);
  }, [preview, onlyAttention]);

  const footer = result ? null : (
    <>
      <Button variant="secondary" onClick={onClose} disabled={confirming}>
        Отмена
      </Button>
      <Button
        onClick={() => void confirm()}
        loading={confirming}
        disabled={!preview || previewing || importableCount === 0 || unresolvedMatches > 0}
      >
        {preview ? `Подтвердить импорт (${importableCount})` : "Подтвердить импорт"}
      </Button>
    </>
  );

  return (
    <Modal
      open
      onClose={onClose}
      title="Импорт графика из Excel"
      description="Файл проверяется до записи: сначала предпросмотр, затем подтверждение. Файл не сохраняется на сервере."
      size="lg"
      footer={footer}
    >
      {/* Итог импорта */}
      {result && (
        <div className="schedule-import-result">
          <h3>Импорт завершён</h3>
          <ul className="schedule-import-counters">
            <li>
              Создано кандидатов: <strong>{result.created}</strong>
            </li>
            <li>
              Сопоставлено: <strong>{result.matched + result.updated}</strong>
              {result.updated > 0 ? ` (из них обновлено ${result.updated})` : ""}
            </li>
            <li>
              Служебных записей: <strong>{result.service_created}</strong>
            </li>
            <li>
              Пропущено: <strong>{result.skipped}</strong>
            </li>
            <li>
              Ошибок: <strong>{result.errors}</strong>
            </li>
          </ul>
          <div className="schedule-import-result-actions">
            <Button variant="secondary" onClick={downloadReport}>
              Скачать отчёт (CSV)
            </Button>
            <Button variant="secondary" onClick={() => void copyReport()}>
              Копировать отчёт
            </Button>
            <Button onClick={finish}>Готово</Button>
          </div>
        </div>
      )}

      {/* Выбор файла и предпросмотр */}
      {!result && (
        <div className="schedule-import-body">
          <div className="schedule-import-file">
            <input
              type="file"
              accept=".xlsx"
              aria-label="Файл графика (.xlsx)"
              onChange={(event) => void handleFileChange(event.target.files?.[0] ?? null)}
            />
            {fileName && !previewError && !preview && !previewing && (
              <span className="schedule-import-filename">{fileName}</span>
            )}
            {previewing && <span>Разбираем файл…</span>}
          </div>

          {previewError && (
            <p className="schedule-import-error" role="alert">
              {previewError}
            </p>
          )}

          {preview && (
            <>
              <div className="schedule-import-summary">
                <Badge tone="info">строк: {preview.summary.rows_total}</Badge>
                <Badge tone="info">дней: {preview.summary.days_total}</Badge>
                <Badge tone="success">новых: {preview.summary.new_count}</Badge>
                <Badge tone="amber">к сопоставлению: {preview.summary.match_count}</Badge>
                {preview.summary.ambiguous_count > 0 && (
                  <Badge tone="danger">спорных: {preview.summary.ambiguous_count}</Badge>
                )}
                {preview.summary.error_rows > 0 && (
                  <Badge tone="danger">с ошибками: {preview.summary.error_rows}</Badge>
                )}
              </div>
              {preview.days.length > 0 && (
                <p className="schedule-import-hint">
                  Распознанные дни: {formatDay(preview.days[0])}
                  {preview.days.length > 1 && ` — ${formatDay(preview.days[preview.days.length - 1])}`}{" "}
                  ({preview.summary.days_total})
                </p>
              )}
              {preview.warnings.map((warning) => (
                <p key={warning} className="schedule-import-warning">
                  {warning}
                </p>
              ))}
              <p className="schedule-import-hint">
                Совпадение только по ФИО не гарантирует, что это тот же человек: проверьте
                сопоставления перед подтверждением. Повторный импорт того же файла не создаёт
                дубликаты.
              </p>

              <label className="schedule-checkbox">
                <input
                  type="checkbox"
                  checked={onlyAttention}
                  onChange={(event) => setOnlyAttention(event.target.checked)}
                />
                Показать только требующие внимания ({preview.rows.filter(needsAttention).length})
              </label>

              <div className="schedule-import-table-wrap">
                <table className="schedule-table schedule-import-table">
                  <thead>
                    <tr>
                      <th scope="col">Строка</th>
                      <th scope="col">Дата</th>
                      <th scope="col">Время</th>
                      <th scope="col">ФИО / текст</th>
                      <th scope="col">Организация · Отдел · Должность</th>
                      <th scope="col">Действие</th>
                      <th scope="col">Примечания</th>
                    </tr>
                  </thead>
                  <tbody>
                    {visibleRows.map((row) => {
                      const decision = effectiveDecision(row, overrides);
                      const candidates = row.match ? [row.match, ...row.match_options] : row.match_options;
                      return (
                        <tr key={row.row_index} className={needsAttention(row) ? "is-attention" : ""}>
                          <td>{row.sheet_row}</td>
                          <td>{formatDay(row.entry_date)}</td>
                          <td>{row.time_display || "—"}</td>
                          <td>
                            {row.full_name ?? "—"}
                            {row.phone_masked && (
                              <span className="schedule-import-phone"> {row.phone_masked}</span>
                            )}
                          </td>
                          <td>
                            {[row.organization, row.department, row.position]
                              .filter(Boolean)
                              .join(" · ") || "—"}
                          </td>
                          <td>
                            <select
                              aria-label={`Действие для строки ${row.sheet_row}`}
                              value={decision.action}
                              onChange={(event) =>
                                setRowAction(row, event.target.value as ImportDecisionAction)
                              }
                            >
                              <option value="skip">{ACTION_LABELS.skip}</option>
                              {row.full_name && <option value="create">{ACTION_LABELS.create}</option>}
                              {candidates.length > 0 && (
                                <option value="match">{ACTION_LABELS.match}</option>
                              )}
                              {(row.full_name || row.comment) && (
                                <option value="service">{ACTION_LABELS.service}</option>
                              )}
                            </select>
                            {decision.action === "match" && candidates.length > 0 && (
                              <select
                                aria-label={`Кандидат для строки ${row.sheet_row}`}
                                value={decision.candidate_id ?? ""}
                                onChange={(event) => setRowCandidate(row, event.target.value)}
                              >
                                <option value="">— выберите кандидата —</option>
                                {candidates.map((candidate) => (
                                  <option key={candidate.candidate_id} value={candidate.candidate_id}>
                                    {candidate.full_name}
                                    {candidate.reason === "phone" ? " (телефон)" : ""}
                                  </option>
                                ))}
                              </select>
                            )}
                          </td>
                          <td>
                            {row.parse_error ? (
                              <span className="schedule-import-cell-error">{row.parse_error}</span>
                            ) : (
                              <>
                                {row.match && decision.action === "match" && !row.match.confident && (
                                  <span className="schedule-import-warning-inline">
                                    совпадение только по ФИО — проверьте
                                  </span>
                                )}
                                {row.already_imported && (
                                  <span className="schedule-import-warning-inline">
                                    уже импортировалась
                                  </span>
                                )}
                                {row.warnings.map((warning) => (
                                  <span key={warning} className="schedule-import-warning-inline">
                                    {warning}
                                  </span>
                                ))}
                              </>
                            )}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>

              {confirmError && (
                <p className="schedule-import-error" role="alert">
                  {confirmError}
                </p>
              )}
              {unresolvedMatches > 0 && (
                <p className="schedule-import-error" role="alert">
                  Для {unresolvedMatches} строк выберите кандидата для сопоставления (или
                  пропустите их).
                </p>
              )}
            </>
          )}
        </div>
      )}

    </Modal>
  );
}
