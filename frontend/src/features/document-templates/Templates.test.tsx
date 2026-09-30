import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import * as api from "../../api";
import { ToastProvider } from "../../design-system/components/Toast";
import type {
  DocumentRenderPreview,
  DocumentTemplate,
  GeneratedDocument,
} from "../../types";
import { GeneratedDocumentsTab } from "./GeneratedDocumentsTab";
import { TemplatesPage } from "./TemplatesPage";
import {
  TEMPLATE_IMPORT_MAX_BYTES,
  describeImportFile,
  friendlyPlaceholderName,
  kindLabel,
} from "./vocabulary";

vi.mock("../../api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../api")>()),
  listDocumentTemplates: vi.fn(),
  listTemplatePlaceholders: vi.fn(),
  createDocumentTemplate: vi.fn(),
  importDocumentTemplate: vi.fn(),
  renameDocumentTemplate: vi.fn(),
  addDocumentTemplateVersion: vi.fn(),
  activateDocumentTemplateVersion: vi.fn(),
  archiveDocumentTemplateVersion: vi.fn(),
  previewCandidateDocument: vi.fn(),
  generateCandidateDocument: vi.fn(),
  listGeneratedDocuments: vi.fn(),
  downloadGeneratedDocument: vi.fn(),
}));

const version = {
  id: "version-1",
  template_id: "template-1",
  number: 1,
  state: "active" as const,
  title: "Оффер для кандидата",
  body: "Здравствуйте, {{ candidate.full_name }}!",
  placeholders: ["candidate.full_name"],
  author_id: "admin-1",
  created_at: "2026-09-20T10:00:00Z",
  activated_at: "2026-09-21T10:00:00Z",
};

const draftVersion = {
  ...version,
  id: "version-2",
  number: 2,
  state: "draft" as const,
  activated_at: null,
};

const template: DocumentTemplate = {
  id: "template-1",
  kind: "offer",
  scope: "offer",
  name: "Оффер (базовый)",
  revision: 3,
  author_id: "admin-1",
  created_at: "2026-09-20T10:00:00Z",
  updated_at: "2026-09-21T10:00:00Z",
  versions: [version, draftVersion],
};

const generated: GeneratedDocument = {
  id: "generation-1",
  candidate_id: "candidate-1",
  template_id: "template-1",
  template_version_id: "version-1",
  template_number: 1,
  template_name: "Оффер (базовый)",
  template_title: "Оффер для кандидата",
  kind: "offer",
  revision: 1,
  body_text: "Здравствуйте, Иванов Иван!",
  content_sha256: "a".repeat(64),
  created_by: "hr-1",
  created_at: "2026-09-22T10:00:00Z",
};

const renderPreview: DocumentRenderPreview = {
  template_id: "template-1",
  template_version_id: "version-1",
  template_number: 1,
  template_name: "Оффер (базовый)",
  kind: "offer",
  title: "Оффер для кандидата",
  body_text: "Здравствуйте, Иванов Иван!",
  body_html: "<p>Здравствуйте, Иванов Иван!</p>",
  placeholders: ["candidate.full_name"],
};

function renderTemplates() {
  return render(
    <ToastProvider>
      <TemplatesPage />
    </ToastProvider>,
  );
}

function renderTab() {
  return render(
    <ToastProvider>
      <GeneratedDocumentsTab candidateId="candidate-1" />
    </ToastProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.listDocumentTemplates).mockResolvedValue({
    items: [template],
    can_manage: true,
  });
  vi.mocked(api.listTemplatePlaceholders).mockResolvedValue({
    items: [
      { token: "candidate.full_name", description: "ФИО кандидата" },
      { token: "system.date", description: "Текущая дата" },
    ],
  });
  vi.mocked(api.listGeneratedDocuments).mockResolvedValue({
    items: [],
    total: 0,
    limit: 20,
    offset: 0,
  });
});

describe("Шаблоны документов", () => {
  it("shows versions, statuses and the published marker", async () => {
    renderTemplates();
    expect(await screen.findByText("Оффер (базовый)")).toBeInTheDocument();
    expect(screen.getByText("Опубликована v1")).toBeInTheDocument();
    expect(
      screen.getByText(/Оффер · Оффер · ревизия 3 · версий 2/),
    ).toBeInTheDocument();
    expect(screen.getByText("Версии и статусы")).toBeInTheDocument();
  });

  it("hides management without the grant and lists only published versions", async () => {
    vi.mocked(api.listDocumentTemplates).mockResolvedValue({
      items: [{ ...template, versions: [version] }],
      can_manage: false,
    });
    renderTemplates();
    await screen.findByText("Оффер (базовый)");
    expect(screen.getByText(/Только просмотр опубликованных версий/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Новый шаблон" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Новая версия" })).toBeDisabled();
    await userEvent.click(screen.getByText("Версии и статусы"));
    expect(screen.queryByRole("button", { name: "Опубликовать" })).toBeNull();
    expect(screen.getByText("только просмотр")).toBeInTheDocument();
  });

  it("creates a template with a controlled kind and stage scope", async () => {
    const user = userEvent.setup();
    vi.mocked(api.createDocumentTemplate).mockResolvedValue(template);
    renderTemplates();
    await screen.findByText("Оффер (базовый)");
    await user.click(screen.getByRole("button", { name: "Новый шаблон" }));
    await user.type(screen.getByLabelText(/^Название/), "Оффер (стажёр)");
    await user.selectOptions(screen.getByLabelText("Этап (область действия)"), "offer");
    await user.type(screen.getByLabelText(/Заголовок документа/), "Оффер");
    await user.type(screen.getByLabelText(/Текст шаблона/), "Здравствуйте!");
    await user.click(screen.getByRole("button", { name: "Сохранить черновик" }));
    await waitFor(() =>
      expect(api.createDocumentTemplate).toHaveBeenCalledWith({
        kind: "offer",
        scope: "offer",
        name: "Оффер (стажёр)",
        title: "Оффер",
        body: "Здравствуйте!",
      }),
    );
  });

  it("inserts an allowlisted placeholder into the body", async () => {
    const user = userEvent.setup();
    renderTemplates();
    await screen.findByText("Оффер (базовый)");
    await user.click(screen.getByRole("button", { name: "Новая версия" }));
    const body = screen.getByLabelText(/Текст шаблона/) as HTMLTextAreaElement;
    expect(body.value).toBe("Здравствуйте, {{ candidate.full_name }}!");
    // The button shows the field a person recognises; the token is in the tooltip.
    const chip = screen.getByRole("button", { name: "Текущая дата" });
    expect(chip).toHaveAttribute("title", expect.stringContaining("{{ system.date }}"));
    await user.click(chip);
    expect(body.value).toContain("{{ system.date }}");
  });

  it("publishes a draft version with the optimistic revision", async () => {
    const user = userEvent.setup();
    vi.mocked(api.activateDocumentTemplateVersion).mockResolvedValue(template);
    renderTemplates();
    await screen.findByText("Оффер (базовый)");
    await user.click(screen.getByText("Версии и статусы"));
    await user.click(screen.getByRole("button", { name: "Опубликовать" }));
    // Публикация заменяет текущую опубликованную версию — требуется подтверждение.
    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveTextContent("Опубликовать версию 2?");
    await user.click(within(dialog).getByRole("button", { name: "Опубликовать" }));
    await waitFor(() =>
      expect(api.activateDocumentTemplateVersion).toHaveBeenCalledWith(
        "template-1",
        "version-2",
        3,
      ),
    );
    expect(await screen.findByText("Версия 2 опубликована")).toBeInTheDocument();
  });

  it("shows a conflict message when the revision moved on", async () => {
    const user = userEvent.setup();
    vi.mocked(api.archiveDocumentTemplateVersion).mockRejectedValue(
      new api.ApiError(409, "Шаблон изменился."),
    );
    renderTemplates();
    await screen.findByText("Оффер (базовый)");
    await user.click(screen.getByText("Версии и статусы"));
    await user.click(screen.getByRole("button", { name: "В архив" }));
    // Архивирование опубликованной версии оставит шаблон без публикации —
    // опасное действие, требуется подтверждение.
    const dialog = await screen.findByRole("alertdialog");
    expect(dialog).toHaveTextContent("Архивировать версию 1?");
    expect(api.archiveDocumentTemplateVersion).not.toHaveBeenCalled();
    await user.click(within(dialog).getByRole("button", { name: "В архив" }));
    expect(await screen.findByText(/Конфликт версии/)).toBeInTheDocument();
  });

  it("renames a template without touching its versions", async () => {
    const user = userEvent.setup();
    vi.mocked(api.renameDocumentTemplate).mockResolvedValue({
      ...template,
      name: "Оффер (2026)",
    });
    renderTemplates();
    await screen.findByText("Оффер (базовый)");
    await user.click(screen.getByRole("button", { name: "Переименовать" }));
    const input = screen.getByLabelText(/^Название/);
    await user.clear(input);
    await user.type(input, "Оффер (2026)");
    await user.click(screen.getByRole("button", { name: "Сохранить имя" }));
    await waitFor(() =>
      expect(api.renameDocumentTemplate).toHaveBeenCalledWith("template-1", {
        name: "Оффер (2026)",
        expected_revision: 3,
      }),
    );
  });

  it("lists the placeholder allowlist", async () => {
    const user = userEvent.setup();
    renderTemplates();
    await screen.findByText("Оффер (базовый)");
    await user.click(screen.getByText("Доступные плейсхолдеры (2)"));
    expect(screen.getByText("{{ candidate.full_name }}")).toBeInTheDocument();
    expect(screen.getByText(/Свободный HTML, выражения и SQL/)).toBeInTheDocument();
  });

  it("shows an empty state with a create CTA when there are no templates", async () => {
    const user = userEvent.setup();
    vi.mocked(api.listDocumentTemplates).mockResolvedValue({
      items: [],
      can_manage: true,
    });
    renderTemplates();

    expect(await screen.findByText("Шаблонов пока нет")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Создать первый шаблон" }));
    expect(
      await screen.findByRole("heading", { name: "Новый шаблон" }),
    ).toBeInTheDocument();
    expect(api.createDocumentTemplate).not.toHaveBeenCalled();
  });

  it("shows an error state and reloads on retry", async () => {
    const user = userEvent.setup();
    vi.mocked(api.listDocumentTemplates).mockRejectedValueOnce(
      new api.ApiError(500, "Сервер временно недоступен."),
    );
    renderTemplates();

    expect(await screen.findByText("Не удалось загрузить данные")).toBeInTheDocument();
    expect(screen.queryByText("Оффер (базовый)")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Повторить попытку" }));
    expect(await screen.findByText("Оффер (базовый)")).toBeInTheDocument();
  });

  it("renders the permission-denied state when listing answers 403", async () => {
    vi.mocked(api.listDocumentTemplates).mockRejectedValue(
      new api.ApiError(403, "Нет права управления шаблонами документов."),
    );
    renderTemplates();

    expect(await screen.findByText("Недостаточно прав")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Новый шаблон" })).not.toBeInTheDocument();
  });

  it("shows template creation and update dates", async () => {
    renderTemplates();
    await screen.findByText("Оффер (базовый)");
    const expected = new Date("2026-09-20T10:00:00Z").toLocaleString("ru-RU");
    const updated = new Date("2026-09-21T10:00:00Z").toLocaleString("ru-RU");
    expect(screen.getByText(`Создан: ${expected} · Изменён: ${updated}`)).toBeInTheDocument();
  });

  it("filters templates by search text and reports an empty result", async () => {
    const user = userEvent.setup();
    renderTemplates();
    await screen.findByText("Оффер (базовый)");

    await user.type(screen.getByLabelText("Поиск шаблона"), "договор");
    expect(await screen.findByText(/Показано 0 из 1/)).toBeInTheDocument();
    expect(screen.getByText("Ничего не найдено")).toBeInTheDocument();
    expect(screen.queryByText("Оффер (базовый)")).not.toBeInTheDocument();

    await user.clear(screen.getByLabelText("Поиск шаблона"));
    await user.type(screen.getByLabelText("Поиск шаблона"), "базовый");
    expect(await screen.findByText(/Показано 1 из 1/)).toBeInTheDocument();
    expect(screen.getByText("Оффер (базовый)")).toBeInTheDocument();
  });

  it("filters templates by publication status", async () => {
    const user = userEvent.setup();
    vi.mocked(api.listDocumentTemplates).mockResolvedValue({
      items: [
        template,
        { ...template, id: "template-2", name: "Анкета (черновик)", versions: [draftVersion] },
      ],
      can_manage: true,
    });
    renderTemplates();
    await screen.findByText("Оффер (базовый)");
    expect(screen.getByText("Анкета (черновик)")).toBeInTheDocument();

    await user.selectOptions(screen.getByLabelText("Статус"), "published");
    expect(await screen.findByText(/Показано 1 из 2/)).toBeInTheDocument();
    expect(screen.getByText("Оффер (базовый)")).toBeInTheDocument();
    expect(screen.queryByText("Анкета (черновик)")).not.toBeInTheDocument();

    await user.selectOptions(screen.getByLabelText("Статус"), "unpublished");
    expect(await screen.findByText(/Показано 1 из 2/)).toBeInTheDocument();
    expect(screen.getByText("Анкета (черновик)")).toBeInTheDocument();
    expect(screen.queryByText("Оффер (базовый)")).not.toBeInTheDocument();
  });

  it("surfaces a backend 422 validation message in Russian", async () => {
    const user = userEvent.setup();
    vi.mocked(api.createDocumentTemplate).mockRejectedValue(
      new api.ApiError(422, "Ошибка запроса (422).", [
        { msg: "Value error, Укажите название шаблона.", type: "value_error" },
      ]),
    );
    renderTemplates();
    await screen.findByText("Оффер (базовый)");
    await user.click(screen.getByRole("button", { name: "Новый шаблон" }));
    await user.type(screen.getByLabelText(/^Название/), " ");
    await user.type(screen.getByLabelText(/Заголовок документа/), "Оффер");
    await user.type(screen.getByLabelText(/Текст шаблона/), "Текст");
    await user.click(screen.getByRole("button", { name: "Сохранить черновик" }));

    expect(await screen.findByText("Укажите название шаблона.")).toBeInTheDocument();
    // Форма осталась открытой — черновик не потерян молча.
    expect(screen.getByRole("heading", { name: "Новый шаблон" })).toBeInTheDocument();
  });

  it("opens the related document lists section as a separate action", async () => {
    const user = userEvent.setup();
    window.location.hash = "#/templates";
    renderTemplates();
    await screen.findByText("Оффер (базовый)");

    await user.click(screen.getByRole("button", { name: "Списки документов" }));
    expect(window.location.hash).toBe("#/documents");
    window.location.hash = "";
  });
});

describe("Документы по шаблону", () => {
  it("offers only published versions and previews without saving", async () => {
    const user = userEvent.setup();
    vi.mocked(api.previewCandidateDocument).mockResolvedValue(renderPreview);
    renderTab();
    await screen.findByText("Документы по шаблону");
    const picker = screen.getByLabelText("Опубликованная версия шаблона");
    expect(picker).toHaveTextContent("Оффер (базовый) — версия 1");
    expect(picker).not.toHaveTextContent("версия 2");
    await user.selectOptions(picker, "version-1");
    await user.click(screen.getByRole("button", { name: "Предпросмотр" }));
    expect(await screen.findByText("Здравствуйте, Иванов Иван!")).toBeInTheDocument();
    expect(api.previewCandidateDocument).toHaveBeenCalledWith("candidate-1", "version-1");
    expect(api.generateCandidateDocument).not.toHaveBeenCalled();
  });

  it("saves an immutable snapshot with an idempotency key", async () => {
    const user = userEvent.setup();
    vi.mocked(api.previewCandidateDocument).mockResolvedValue(renderPreview);
    vi.mocked(api.generateCandidateDocument).mockResolvedValue({
      ...generated,
      body_html: renderPreview.body_html,
    });
    renderTab();
    await screen.findByText("Документы по шаблону");
    await user.selectOptions(
      screen.getByLabelText("Опубликованная версия шаблона"),
      "version-1",
    );
    await user.click(screen.getByRole("button", { name: "Предпросмотр" }));
    await screen.findByText("Здравствуйте, Иванов Иван!");
    await user.click(screen.getByRole("button", { name: "Сохранить документ" }));
    await waitFor(() => expect(api.generateCandidateDocument).toHaveBeenCalledTimes(1));
    const [candidateId, versionId, key] = vi.mocked(api.generateCandidateDocument).mock
      .calls[0];
    expect(candidateId).toBe("candidate-1");
    expect(versionId).toBe("version-1");
    expect(key).toMatch(/[0-9a-f-]{8,}/);
  });

  it("lists saved documents and downloads HTML via an object URL", async () => {
    const user = userEvent.setup();
    vi.mocked(api.listGeneratedDocuments).mockResolvedValue({
      items: [generated],
      total: 1,
      limit: 20,
      offset: 0,
    });
    vi.mocked(api.downloadGeneratedDocument).mockResolvedValue({
      blob: new Blob(["<p>ok</p>"], { type: "text/html" }),
      filename: "document-offer-rev1.html",
    });
    const createObjectURL = vi.fn(() => "blob:document");
    const revokeObjectURL = vi.fn();
    vi.stubGlobal("URL", { ...URL, createObjectURL, revokeObjectURL });
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    renderTab();
    expect(await screen.findByText(/Оффер \(базовый\) · v1/)).toBeInTheDocument();
    expect(screen.getByText(/sha256: aaaaaaaaaaaaaaaa/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "HTML" }));
    await waitFor(() =>
      expect(api.downloadGeneratedDocument).toHaveBeenCalledWith(
        "candidate-1",
        "generation-1",
        "html",
      ),
    );
    expect(createObjectURL).toHaveBeenCalled();
    expect(click).toHaveBeenCalled();
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:document");
    expect(screen.getByText(/печатается в PDF средствами браузера/)).toBeInTheDocument();
    vi.unstubAllGlobals();
    click.mockRestore();
  });

  it("surfaces the backend 422 when the template scope does not match the stage", async () => {
    const user = userEvent.setup();
    vi.mocked(api.previewCandidateDocument).mockRejectedValue(
      new api.ApiError(
        422,
        "Шаблон применим только к этапу «Оффер». Текущий этап кандидата: «Новый».",
      ),
    );
    renderTab();
    await screen.findByText("Документы по шаблону");
    await user.selectOptions(
      screen.getByLabelText("Опубликованная версия шаблона"),
      "version-1",
    );
    await user.click(screen.getByRole("button", { name: "Предпросмотр" }));
    expect(
      await screen.findByText(
        /Шаблон применим только к этапу «Оффер»\. Текущий этап кандидата: «Новый»\./,
      ),
    ).toBeInTheDocument();
    expect(api.generateCandidateDocument).not.toHaveBeenCalled();
  });

  it("explains why nothing can be generated without published templates", async () => {
    vi.mocked(api.listDocumentTemplates).mockResolvedValue({
      items: [{ ...template, versions: [draftVersion] }],
      can_manage: false,
    });
    renderTab();
    expect(
      await screen.findByText(/Нет опубликованных шаблонов/),
    ).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Block E — methodical base vocabulary (UX feedback 2026-09-29)
// ---------------------------------------------------------------------------

describe("vocabulary of the methodical base", () => {
  it("labels every kind a person would use, keeping the stored keys", () => {
    expect(kindLabel("document")).toBe("Документ");
    expect(kindLabel("checklist")).toBe("Чек-лист");
    expect(kindLabel("interview")).toBe("Вопросник для интервью");
    expect(kindLabel("memo")).toBe("Памятка HR");
    expect(kindLabel("script")).toBe("Скрипт");
    // Pre-existing rows keep rendering under their own name.
    expect(kindLabel("offer")).toBe("Оффер");
    expect(kindLabel("unknown_key")).toBe("unknown_key");
  });

  it("shows the field a person recognises, not the stored token", () => {
    expect(
      friendlyPlaceholderName({ token: "candidate.full_name", description: "ФИО кандидата" }),
    ).toBe("ФИО кандидата");
    // A token without a description still renders as something readable.
    expect(friendlyPlaceholderName({ token: "system.date", description: "" })).toBe("date");
  });

  it("flags an unsupported or oversized file before uploading", () => {
    expect(describeImportFile(null)).toMatch(/Выберите файл/);
    const pdf = new File(["x"], "offer.pdf", { type: "application/pdf" });
    expect(describeImportFile(pdf)).toMatch(/не поддерживается/);
    const empty = new File([], "script.md");
    expect(describeImportFile(empty)).toMatch(/пуст/);
    const big = {
      name: "big.md",
      size: TEMPLATE_IMPORT_MAX_BYTES + 1,
    } as File;
    expect(describeImportFile(big)).toMatch(/КБ/);
    expect(describeImportFile(new File(["текст"], "script.md"))).toBeNull();
  });
});

describe("importing methodical material", () => {
  it("warns that a legal form has to be re-checked by a person", async () => {
    const user = userEvent.setup();
    renderTemplates();
    expect(await screen.findByText(/Правовая форма/)).toBeInTheDocument();

    // A non-legal kind gets no such banner. The filters are on one screen, so
    // drive the real UI instead of re-rendering over it.
    vi.mocked(api.listDocumentTemplates).mockResolvedValue({
      items: [{ ...template, kind: "checklist", name: "Чек-лист интервью" }],
      can_manage: true,
    });
    await user.type(screen.getByLabelText(/^Поиск шаблона/), "Чек-лист");
    expect(screen.queryByText(/Правовая форма/)).not.toBeInTheDocument();
  });

  it("imports a chosen file as a draft", async () => {
    const user = userEvent.setup();
    vi.mocked(api.importDocumentTemplate).mockResolvedValue(template);
    renderTemplates();
    await user.click(await screen.findByRole("button", { name: /Загрузить из файла/ }));

    const fileInput = await screen.findByLabelText(/^Файл/);
    const file = new File(["# Скрипт\n\nТекст"], "Скрипт звонка.md", {
      type: "text/markdown",
    });
    await user.upload(fileInput, file);
    await user.click(screen.getByRole("button", { name: /Загрузить как черновик/ }));

    await waitFor(() => {
      expect(vi.mocked(api.importDocumentTemplate)).toHaveBeenCalledWith({
        kind: "checklist",
        scope: "",
        name: "",
        file: expect.any(File),
      });
    });
    expect(
      await screen.findByText(/Шаблон загружен из файла/),
    ).toBeInTheDocument();
  });

  it("states the accepted formats and blocks submitting without a file", async () => {
    const user = userEvent.setup();
    renderTemplates();
    await user.click(await screen.findByRole("button", { name: /Загрузить из файла/ }));
    const fileInput = await screen.findByLabelText(/^Файл/);

    // The hint is the contract with the user: which formats, how big, UTF-8.
    const hintId = fileInput.getAttribute("aria-describedby") ?? "";
    expect(document.getElementById(hintId)).toHaveTextContent(
      ".txt, .md, .markdown, .csv, до 512 КБ, UTF-8",
    );
    // The picker itself is limited to those formats, and nothing is sent yet.
    expect(fileInput).toHaveAttribute("accept", ".txt,.md,.markdown,.csv");
    expect(screen.getByRole("button", { name: /Загрузить как черновик/ })).toBeDisabled();

    await user.upload(fileInput, new File(["# Скрипт"], "script.md"));
    expect(screen.getByRole("button", { name: /Загрузить как черновик/ })).toBeEnabled();
  });

  it("surfaces the server refusal instead of pretending the import worked", async () => {
    const user = userEvent.setup();
    vi.mocked(api.importDocumentTemplate).mockRejectedValue(
      new api.ApiError(422, "Файл не похож на текст: внутри обнаружены двоичные данные."),
    );
    renderTemplates();
    await user.click(await screen.findByRole("button", { name: /Загрузить из файла/ }));
    await user.upload(
      await screen.findByLabelText(/^Файл/),
      new File(["текст"], "note.md"),
    );
    await user.click(screen.getByRole("button", { name: /Загрузить как черновик/ }));

    expect(
      await screen.findByText(/внутри обнаружены двоичные данные/),
    ).toBeInTheDocument();
  });
});
