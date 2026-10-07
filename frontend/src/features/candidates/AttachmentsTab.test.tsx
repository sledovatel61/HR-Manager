import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../../api";
import type { CandidateAttachment, CandidateAttachmentList } from "../../types";
import { AttachmentsTab } from "./AttachmentsTab";

vi.mock("../../api", async (importOriginal) => {
  const original = await importOriginal<typeof import("../../api")>();
  return {
    ...original,
    listCandidateAttachments: vi.fn(),
    uploadCandidateAttachment: vi.fn(),
    deleteCandidateAttachment: vi.fn(),
    fetchCandidateAttachment: vi.fn(),
    saveCandidateAttachmentWithPicker: vi.fn(),
    saveDialogAvailable: vi.fn(),
    triggerBrowserDownload: vi.fn(),
    openSavedFile: vi.fn(),
  };
});

import * as api from "../../api";

const CANDIDATE_ID = "44444444-4444-4444-4444-444444444444";

const ATTACHMENT: CandidateAttachment = {
  id: "aaaaaaaa-1111-2222-3333-444444444444",
  candidate_id: CANDIDATE_ID,
  filename: "Анкета кандидата.docx",
  kind: "docx",
  size_bytes: 2048,
  sha256: "0".repeat(64),
  uploaded_by_user_id: "22222222-2222-2222-2222-222222222222",
  uploaded_by_username: "hr1",
  uploaded_at: "2026-09-30T10:15:00Z",
};

function payload(overrides: Partial<CandidateAttachmentList> = {}): CandidateAttachmentList {
  return {
    items: [ATTACHMENT],
    total: 1,
    total_bytes: ATTACHMENT.size_bytes,
    limits: {
      max_file_bytes: 10 * 1024 * 1024,
      max_total_bytes: 100 * 1024 * 1024,
      max_count: 30,
    },
    can_manage: true,
    ...overrides,
  };
}

const listMock = vi.mocked(api.listCandidateAttachments);
const uploadMock = vi.mocked(api.uploadCandidateAttachment);
const removeMock = vi.mocked(api.deleteCandidateAttachment);
const fetchMock = vi.mocked(api.fetchCandidateAttachment);
const savePickerMock = vi.mocked(api.saveCandidateAttachmentWithPicker);
const pickerAvailableMock = vi.mocked(api.saveDialogAvailable);
const browserDownloadMock = vi.mocked(api.triggerBrowserDownload);
const openSavedMock = vi.mocked(api.openSavedFile);

beforeEach(() => {
  vi.clearAllMocks();
  listMock.mockResolvedValue(payload());
  pickerAvailableMock.mockReturnValue(false);
});

describe("Список вложений", () => {
  it("показывает имя, тип, размер, дату и автора", async () => {
    render(<AttachmentsTab candidateId={CANDIDATE_ID} />);

    expect(await screen.findByText("Анкета кандидата.docx")).toBeInTheDocument();
    const item = screen.getByText("Анкета кандидата.docx").closest("li");
    expect(item).not.toBeNull();
    const meta = within(item as HTMLElement).getByText(/Документ Word/);
    expect(meta.textContent).toContain("2 КБ");
    expect(meta.textContent).toContain("hr1");
    expect(meta.textContent).toContain("30.09.2026");
  });

  it("показывает пустое состояние", async () => {
    listMock.mockResolvedValue(payload({ items: [], total: 0, total_bytes: 0 }));
    render(<AttachmentsTab candidateId={CANDIDATE_ID} />);

    expect(await screen.findByText("Вложений пока нет.")).toBeInTheDocument();
  });
});

describe("Загрузка", () => {
  it("загружает .docx и обновляет список", async () => {
    uploadMock.mockImplementation((_candidateId, _file, onProgress) => {
      onProgress?.(42);
      onProgress?.(100);
      return Promise.resolve(ATTACHMENT);
    });
    const user = userEvent.setup();
    render(<AttachmentsTab candidateId={CANDIDATE_ID} />);
    await screen.findByText("Анкета кандидата.docx");

    const input = screen.getByLabelText("Файл анкеты или скана") as HTMLInputElement;
    const file = new File(["docx-bytes"], "Новая анкета.docx", {
      type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    });
    await user.upload(input, file);

    await waitFor(() => expect(uploadMock).toHaveBeenCalledTimes(1));
    expect(uploadMock.mock.calls[0][1]).toBe(file);
    // Имя в ответе — уже очищенное сервером, а не то, что прислал браузер.
    expect(await screen.findByText(/Файл «Анкета кандидата.docx» загружен/)).toBeInTheDocument();
    // Список перечитан после загрузки.
    expect(listMock).toHaveBeenCalledTimes(2);
  });

  it("показывает текст сервера при отказе 413", async () => {
    uploadMock.mockRejectedValue(new ApiError(413, "Файл больше допустимого размера 10 МБ."));
    const user = userEvent.setup();
    render(<AttachmentsTab candidateId={CANDIDATE_ID} />);
    await screen.findByText("Анкета кандидата.docx");

    const input = screen.getByLabelText("Файл анкеты или скана") as HTMLInputElement;
    await user.upload(
      input,
      new File(["x"], "Большой.pdf", { type: "application/pdf" }),
    );

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("Файл больше допустимого размера 10 МБ.");
  });

  it("не отправляет файл запрещённого формата", async () => {
    render(<AttachmentsTab candidateId={CANDIDATE_ID} />);
    await screen.findByText("Анкета кандидата.docx");

    const input = screen.getByLabelText("Файл анкеты или скана") as HTMLInputElement;
    // В диалоге Windows пользователь может выбрать «Все файлы», поэтому
    // компонент обязан отказать сам, а не полагаться на атрибут accept.
    // userEvent фильтрует файлы по accept, поэтому подкладываем файл напрямую.
    const executable = new File(["MZ"], "program.exe", { type: "application/x-msdownload" });
    Object.defineProperty(input, "files", { value: [executable], configurable: true });
    fireEvent.change(input);

    expect(uploadMock).not.toHaveBeenCalled();
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain(".docx и .pdf");
  });

  it("не отправляет файл больше лимита", async () => {
    listMock.mockResolvedValue(
      payload({ limits: { max_file_bytes: 1024, max_total_bytes: 10000000, max_count: 30 } }),
    );
    const user = userEvent.setup();
    render(<AttachmentsTab candidateId={CANDIDATE_ID} />);
    await screen.findByText("Анкета кандидата.docx");

    const input = screen.getByLabelText("Файл анкеты или скана") as HTMLInputElement;
    await user.upload(input, new File(["y".repeat(4096)], "Тяжёлый.pdf", { type: "application/pdf" }));

    expect(uploadMock).not.toHaveBeenCalled();
    expect((await screen.findByRole("alert")).textContent).toContain("больше допустимого размера");
  });
});

describe("Скачивание", () => {
  it("отдаёт файл в загрузки браузера и честно предупреждает о пределе браузера", async () => {
    const blob = new Blob(["docx-bytes"]);
    fetchMock.mockResolvedValue({ blob, filename: "Анкета кандидата.docx" });
    const user = userEvent.setup();
    render(<AttachmentsTab candidateId={CANDIDATE_ID} />);

    await user.click(await screen.findByRole("button", { name: "Скачать" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(CANDIDATE_ID, ATTACHMENT.id));
    expect(browserDownloadMock).toHaveBeenCalledWith(blob, "Анкета кандидата.docx");
    const status = await screen.findByRole("status");
    expect(status.textContent).toContain("передан в загрузки браузера");
    expect(status.textContent).toContain("не может открыть файл в Word или Acrobat");
  });

  it("показывает ошибку скачивания", async () => {
    fetchMock.mockRejectedValue(new ApiError(404, "Вложение не найдено."));
    const user = userEvent.setup();
    render(<AttachmentsTab candidateId={CANDIDATE_ID} />);

    await user.click(await screen.findByRole("button", { name: "Скачать" }));

    expect((await screen.findByRole("alert")).textContent).toContain("Вложение не найдено.");
    expect(browserDownloadMock).not.toHaveBeenCalled();
  });
});

describe("Сохранение через системный диалог", () => {
  it("после реального сохранения предлагает открыть сохранённые байты", async () => {
    pickerAvailableMock.mockReturnValue(true);
    const handle = { getFile: vi.fn() } as unknown as FileSystemFileHandle;
    savePickerMock.mockResolvedValue({
      outcome: "saved",
      filename: "Анкета кандидата.docx",
      handle,
    });
    const user = userEvent.setup();
    render(<AttachmentsTab candidateId={CANDIDATE_ID} />);

    expect(screen.queryByRole("button", { name: "Открыть скачанный файл" })).toBeNull();
    await user.click(await screen.findByRole("button", { name: "Сохранить как…" }));

    expect(await screen.findByText(/Файл «Анкета кандидата.docx» сохранён/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Открыть скачанный файл" }));
    await waitFor(() => expect(openSavedMock).toHaveBeenCalledWith(handle));
  });

  it("отмена диалога не считается успехом", async () => {
    pickerAvailableMock.mockReturnValue(true);
    savePickerMock.mockResolvedValue({ outcome: "cancelled", filename: "Анкета кандидата.docx" });
    const user = userEvent.setup();
    render(<AttachmentsTab candidateId={CANDIDATE_ID} />);

    await user.click(await screen.findByRole("button", { name: "Сохранить как…" }));

    expect(await screen.findByText("Сохранение отменено.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Открыть скачанный файл" })).toBeNull();
    expect(openSavedMock).not.toHaveBeenCalled();
  });

  it("без поддержки API предлагает только скачивание браузером", async () => {
    pickerAvailableMock.mockReturnValue(false);
    render(<AttachmentsTab candidateId={CANDIDATE_ID} />);
    await screen.findByText("Анкета кандидата.docx");

    expect(screen.queryByRole("button", { name: "Сохранить как…" })).toBeNull();
    expect(screen.getByText(/Диалог «Сохранить как…» в этом браузере недоступен/)).toBeInTheDocument();
  });
});

describe("Права", () => {
  it("без права управления загрузка и удаление недоступны", async () => {
    listMock.mockResolvedValue(payload({ can_manage: false }));
    render(<AttachmentsTab candidateId={CANDIDATE_ID} />);
    await screen.findByText("Анкета кандидата.docx");

    expect(screen.getByRole("button", { name: "Загрузить анкету" })).toBeDisabled();
    expect(
      screen.queryByRole("button", { name: /Удалить Анкета кандидата.docx/ }),
    ).toBeNull();
    expect(
      screen.getByText("Загрузка и удаление доступны ответственному за кандидата"),
    ).toBeInTheDocument();
    // Скачивание при этом доступно: право видеть кандидата уже есть.
    expect(screen.getByRole("button", { name: "Скачать" })).toBeEnabled();
  });

  it("при достижении лимита загрузка заблокирована", async () => {
    listMock.mockResolvedValue(payload({ total: 30 }));
    render(<AttachmentsTab candidateId={CANDIDATE_ID} />);
    await screen.findByText("Анкета кандидата.docx");

    expect(screen.getByRole("button", { name: "Загрузить анкету" })).toBeDisabled();
  });
});

describe("Удаление", () => {
  it("требует подтверждения, удаляет и обновляет список", async () => {
    removeMock.mockResolvedValue(undefined);
    const user = userEvent.setup();
    render(<AttachmentsTab candidateId={CANDIDATE_ID} />);

    await user.click(await screen.findByRole("button", { name: /Удалить Анкета кандидата.docx/ }));
    expect(removeMock).not.toHaveBeenCalled();
    expect(screen.getByText("Удалить вложение?")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Удалить" }));

    await waitFor(() => expect(removeMock).toHaveBeenCalledWith(CANDIDATE_ID, ATTACHMENT.id));
    expect(await screen.findByText(/удалён из карточки/)).toBeInTheDocument();
    expect(listMock).toHaveBeenCalledTimes(2);
  });

  it("показывает ошибку удаления", async () => {
    removeMock.mockRejectedValue(new ApiError(403, "Недостаточно прав."));
    const user = userEvent.setup();
    render(<AttachmentsTab candidateId={CANDIDATE_ID} />);

    await user.click(await screen.findByRole("button", { name: /Удалить Анкета кандидата.docx/ }));
    await user.click(screen.getByRole("button", { name: "Удалить" }));

    expect((await screen.findByRole("alert")).textContent).toContain("Недостаточно прав.");
  });
});


it("allows selecting the same file again and refreshes the parent attachment badge", async () => {
  uploadMock.mockResolvedValue(ATTACHMENT);
  const onChanged = vi.fn();
  render(<AttachmentsTab candidateId={CANDIDATE_ID} onChanged={onChanged} />);
  await screen.findByText("Анкета кандидата.docx");
  const input = screen.getByLabelText("Файл анкеты или скана");
  const file = new File(["pdf"], "анкета.pdf", { type: "application/pdf" });
  await userEvent.upload(input, file);
  await waitFor(() => expect(onChanged).toHaveBeenCalledTimes(1));
  await userEvent.upload(input, file);
  await waitFor(() => expect(onChanged).toHaveBeenCalledTimes(2));
  expect(uploadMock).toHaveBeenCalledTimes(2);
});

it("shows real upload progress and disables both upload entry points until completion", async () => {
  let finish!: (value: CandidateAttachment) => void;
  uploadMock.mockImplementation((_id, _file, progress) => {
    progress?.(42);
    return new Promise((resolve) => { finish = resolve; });
  });
  render(<AttachmentsTab candidateId={CANDIDATE_ID} />);
  await screen.findByText("Анкета кандидата.docx");
  const input = screen.getByLabelText("Файл анкеты или скана");
  await userEvent.upload(input, new File(["pdf"], "анкета.pdf", { type: "application/pdf" }));
  expect(screen.getByRole("progressbar", { name: "Загрузка файла" })).toHaveAttribute("value", "42");
  expect(input).toBeDisabled();
  expect(screen.getByRole("button", { name: "Загрузить анкету" })).toBeDisabled();
  finish(ATTACHMENT);
  await waitFor(() => expect(input).toBeEnabled());
});

it("reports an upload permission refusal without announcing success", async () => {
  uploadMock.mockRejectedValue(new ApiError(403, "Недостаточно прав."));
  const onChanged = vi.fn();
  render(<AttachmentsTab candidateId={CANDIDATE_ID} onChanged={onChanged} />);
  await screen.findByText("Анкета кандидата.docx");
  await userEvent.upload(screen.getByLabelText("Файл анкеты или скана"), new File(["pdf"], "анкета.pdf", { type: "application/pdf" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Недостаточно прав.");
  expect(onChanged).not.toHaveBeenCalled();
});
