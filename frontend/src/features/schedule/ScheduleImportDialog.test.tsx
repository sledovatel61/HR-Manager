import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../../api";
import { ToastProvider } from "../../design-system/components/Toast";
import type {
  ImportPreviewSummary,
  ImportRowPreview,
  WorkScheduleImportPreview,
} from "../../types";
import { ScheduleImportDialog } from "./ScheduleImportDialog";

vi.mock("../../api", async (importOriginal) => {
  const original = await importOriginal<typeof import("../../api")>();
  return {
    ...original,
    previewWorkScheduleImport: vi.fn(),
    confirmWorkScheduleImport: vi.fn(),
  };
});

import * as api from "../../api";

function row(overrides: Partial<ImportRowPreview>): ImportRowPreview {
  return {
    row_index: 0,
    sheet_row: 5,
    entry_date: "2026-08-10",
    full_name: "Новикова Мария Петровна",
    time_display: "09:15",
    time_from: "09:15:00",
    time_to: null,
    organization: "ООО Авион",
    department: "Цех Сокол",
    position: "Грузчик",
    shift: "1 смена",
    comment: null,
    phone_masked: null,
    kind: "candidate",
    name_confidence: "full",
    suggested_action: "create",
    match: null,
    match_options: [],
    already_imported: false,
    warnings: [],
    parse_error: null,
    ...overrides,
  };
}

function summary(overrides: Partial<ImportPreviewSummary> = {}): ImportPreviewSummary {
  return {
    rows_total: 6,
    days_total: 2,
    candidate_rows: 4,
    service_rows: 1,
    skipped_rows: 1,
    error_rows: 1,
    new_count: 1,
    match_count: 2,
    ambiguous_count: 1,
    ...overrides,
  };
}

function preview(rows: ImportRowPreview[]): WorkScheduleImportPreview {
  return {
    file_name: "график выходов.xlsx",
    file_sha256: "abc123",
    sheet_title: "Лист1",
    days: ["2026-08-10", "2026-08-11"],
    warnings: [],
    rows,
    summary: summary(),
  };
}

const NEW_ROW = row({ row_index: 0, sheet_row: 5 });

const PHONE_MATCH_ROW = row({
  row_index: 1,
  sheet_row: 8,
  full_name: "Смирнов Олег Павлович",
  time_display: "08:00",
  suggested_action: "match",
  match: {
    candidate_id: "cand-phone",
    full_name: "Смирнов Олег Павлович",
    stage: "offer",
    reason: "phone",
    confident: true,
  },
});

const AMBIGUOUS_ROW = row({
  row_index: 2,
  sheet_row: 12,
  full_name: "Кузнецов Пётр",
  name_confidence: "partial",
  time_display: "10:00",
  suggested_action: "match",
  match_options: [
    {
      candidate_id: "cand-k1",
      full_name: "Кузнецов Пётр Алексеевич",
      stage: "interview_done",
      reason: "exact_name",
      confident: false,
    },
    {
      candidate_id: "cand-k2",
      full_name: "Кузнецов П. А.",
      stage: "offer",
      reason: "partial",
      confident: false,
    },
  ],
});

const SERVICE_ROW = row({
  row_index: 3,
  sheet_row: 20,
  full_name: "Увольнение",
  kind: "service",
  name_confidence: null,
  time_display: "13:00–14:00",
  time_from: "13:00:00",
  time_to: "14:00:00",
  suggested_action: "service",
});

const DUPLICATE_ROW = row({
  row_index: 4,
  sheet_row: 31,
  full_name: "Тестова Анна Ивановна",
  time_display: "09:00",
  suggested_action: "skip",
  already_imported: true,
});

const ERROR_ROW = row({
  row_index: 5,
  sheet_row: 40,
  full_name: "Битова Ирина",
  time_display: "",
  time_from: null,
  suggested_action: "skip",
  parse_error: "Не удалось разобрать время «после мед осмотра»",
});

const ALL_ROWS = [NEW_ROW, PHONE_MATCH_ROW, AMBIGUOUS_ROW, SERVICE_ROW, DUPLICATE_ROW, ERROR_ROW];

function renderDialog(props: Partial<Parameters<typeof ScheduleImportDialog>[0]> = {}) {
  const onClose = vi.fn();
  const onImported = vi.fn();
  const view = render(
    <ToastProvider>
      <ScheduleImportDialog onClose={onClose} onImported={onImported} {...props} />
    </ToastProvider>
  );
  return { onClose, onImported, ...view };
}

const XLSX_FILE = new File(["fake xlsx bytes"], "график выходов.xlsx", {
  type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
});

beforeEach(() => {
  vi.clearAllMocks();
});

describe("Импорт графика из Excel (диалог)", () => {
  it("после выбора файла показывает предпросмотр и ничего не записывает", async () => {
    vi.mocked(api.previewWorkScheduleImport).mockResolvedValue(preview(ALL_ROWS));
    const user = userEvent.setup();
    renderDialog();

    await user.upload(screen.getByLabelText("Файл графика (.xlsx)"), XLSX_FILE);

    await waitFor(() => {
      expect(api.previewWorkScheduleImport).toHaveBeenCalledWith(XLSX_FILE);
    });

    // Итоги предпросмотра видны до любой записи.
    expect(await screen.findByText("строк: 6")).toBeInTheDocument();
    expect(screen.getByText("новых: 1")).toBeInTheDocument();
    expect(screen.getByText("спорных: 1")).toBeInTheDocument();
    expect(screen.getByText("Новикова Мария Петровна")).toBeInTheDocument();
    // Распознанные дни файла видны в предпросмотре.
    expect(screen.getByText(/Распознанные дни: 10\.08\.2026 — 11\.08\.2026 \(2\)/)).toBeInTheDocument();

    // Подтверждение требует явного выбора по спорной строке — запись не стартует сама.
    const confirmButton = screen.getByRole("button", { name: /Подтвердить импорт/ });
    expect(confirmButton).toBeDisabled();
    expect(api.confirmWorkScheduleImport).not.toHaveBeenCalled();
  });

  it("отклоняет файл не в формате .xlsx без обращения к серверу", async () => {
    // applyAccept: false — jsdom не фильтрует по accept, проверяем свою валидацию.
    const user = userEvent.setup({ applyAccept: false });
    renderDialog();
    const xlsm = new File(["bytes"], "график с макросами.xlsm", { type: "application/octet-stream" });

    await user.upload(screen.getByLabelText("Файл графика (.xlsx)"), xlsm);

    expect(
      await screen.findByText("Нужен файл графика в формате .xlsx (без макросов).")
    ).toBeInTheDocument();
    expect(api.previewWorkScheduleImport).not.toHaveBeenCalled();
  });

  it("показывает ошибку предпросмотра и не даёт подтвердить импорт", async () => {
    vi.mocked(api.previewWorkScheduleImport).mockRejectedValue(
      new ApiError(422, "Не найдена строка заголовка: ФИО и дата/время.")
    );
    const user = userEvent.setup();
    renderDialog();

    await user.upload(screen.getByLabelText("Файл графика (.xlsx)"), XLSX_FILE);

    expect(
      await screen.findByText("Не найдена строка заголовка: ФИО и дата/время.")
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Подтвердить импорт/ })).toBeDisabled();
    expect(api.confirmWorkScheduleImport).not.toHaveBeenCalled();
  });

  it("помечает строку, которая уже импортировалась (повтор без дублей)", async () => {
    vi.mocked(api.previewWorkScheduleImport).mockResolvedValue(preview(ALL_ROWS));
    const user = userEvent.setup();
    renderDialog();

    await user.upload(screen.getByLabelText("Файл графика (.xlsx)"), XLSX_FILE);
    expect(await screen.findByText("уже импортировалась")).toBeInTheDocument();
  });

  it("требует выбрать кандидата для спорного ФИО и передаёт выбор при подтверждении", async () => {
    vi.mocked(api.previewWorkScheduleImport).mockResolvedValue(preview(ALL_ROWS));
    vi.mocked(api.confirmWorkScheduleImport).mockResolvedValue({
      import_id: "imp-12345678",
      created: 1,
      matched: 2,
      updated: 0,
      service_created: 1,
      skipped: 2,
      errors: 0,
      rows: [],
      report_csv: "Строка;Дата;Время;ФИО;Действие;Результат;Кандидат;Причина",
    });
    const user = userEvent.setup();
    const { onClose, onImported } = renderDialog();

    await user.upload(screen.getByLabelText("Файл графика (.xlsx)"), XLSX_FILE);
    await screen.findByText("строк: 6");

    // Спорная строка: сопоставление без выбора кандидата блокирует подтверждение.
    const confirmButton = screen.getByRole("button", { name: /Подтвердить импорт/ });
    expect(confirmButton).toBeDisabled();
    expect(
      screen.getByText("Для 1 строк выберите кандидата для сопоставления (или пропустите их).")
    ).toBeInTheDocument();

    // Пользователь выбирает конкретного кандидата и меняет служебную строку на пропуск.
    await user.selectOptions(screen.getByLabelText("Кандидат для строки 12"), "cand-k2");
    await user.selectOptions(screen.getByLabelText("Действие для строки 20"), "skip");

    expect(confirmButton).toBeEnabled();
    expect(confirmButton).toHaveTextContent("Подтвердить импорт (3)");
    await user.click(confirmButton);

    expect(api.confirmWorkScheduleImport).toHaveBeenCalledTimes(1);
    const [file, decisions] = vi.mocked(api.confirmWorkScheduleImport).mock.calls[0];
    expect(file).toBe(XLSX_FILE);
    const byIndex = Object.fromEntries(decisions.map((decision) => [decision.row_index, decision]));
    expect(byIndex[2]).toEqual({ row_index: 2, action: "match", candidate_id: "cand-k2" });
    expect(byIndex[3]).toEqual({ row_index: 3, action: "skip", candidate_id: null });
    expect(byIndex[1]).toEqual({
      row_index: 1,
      action: "match",
      candidate_id: "cand-phone",
    });

    // Итог: счётчики, скачивание отчёта и возврат к графику.
    expect(await screen.findByText("Импорт завершён")).toBeInTheDocument();
    expect(screen.getByText("Создано кандидатов:")).toBeInTheDocument();
    expect(screen.getByText("Служебных записей:")).toBeInTheDocument();

    const createObjectUrl = vi.fn(() => "blob:report");
    const revokeObjectUrl = vi.fn();
    vi.stubGlobal("URL", { ...URL, createObjectURL: createObjectUrl, revokeObjectURL: revokeObjectUrl });
    const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    await user.click(screen.getByRole("button", { name: "Скачать отчёт (CSV)" }));
    expect(createObjectUrl).toHaveBeenCalledTimes(1);
    expect(clickSpy).toHaveBeenCalledTimes(1);
    expect(revokeObjectUrl).toHaveBeenCalledWith("blob:report");
    clickSpy.mockRestore();
    vi.unstubAllGlobals();

    // Отчёт можно и скопировать в буфер обмена.
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(window.navigator, "clipboard", {
      value: { writeText },
      configurable: true,
    });
    await user.click(screen.getByRole("button", { name: "Копировать отчёт" }));
    expect(writeText).toHaveBeenCalledWith(expect.stringContaining("Строка;Дата;Время"));

    await user.click(screen.getByRole("button", { name: "Готово" }));
    expect(onImported).toHaveBeenCalledTimes(1);
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("показывает предупреждение о совпадении только по ФИО", async () => {
    const withNameOnlyMatch = preview([
      row({
        row_index: 0,
        sheet_row: 7,
        suggested_action: "match",
        match: {
          candidate_id: "cand-name",
          full_name: "Иванов Иван Иванович",
          stage: "new",
          reason: "exact_name",
          confident: false,
        },
      }),
    ]);
    vi.mocked(api.previewWorkScheduleImport).mockResolvedValue(withNameOnlyMatch);
    const user = userEvent.setup();
    renderDialog();

    await user.upload(screen.getByLabelText("Файл графика (.xlsx)"), XLSX_FILE);

    expect(
      await screen.findByText("совпадение только по ФИО — проверьте")
    ).toBeInTheDocument();
  });

  it("показывает ошибку сервера при подтверждении и не закрывает диалог", async () => {
    vi.mocked(api.previewWorkScheduleImport).mockResolvedValue(preview([NEW_ROW]));
    vi.mocked(api.confirmWorkScheduleImport).mockRejectedValue(
      new ApiError(422, "Кандидат уже изменился — обновите предпросмотр.")
    );
    const user = userEvent.setup();
    const { onClose } = renderDialog();

    await user.upload(screen.getByLabelText("Файл графика (.xlsx)"), XLSX_FILE);
    await screen.findByText("строк: 6");

    await user.click(screen.getByRole("button", { name: /Подтвердить импорт \(1\)/ }));

    expect(
      await screen.findByText("Кандидат уже изменился — обновите предпросмотр.")
    ).toBeInTheDocument();
    expect(onClose).not.toHaveBeenCalled();
  });
});
