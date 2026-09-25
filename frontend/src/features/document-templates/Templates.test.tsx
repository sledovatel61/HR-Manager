import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
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

vi.mock("../../api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../api")>()),
  listDocumentTemplates: vi.fn(),
  listTemplatePlaceholders: vi.fn(),
  createDocumentTemplate: vi.fn(),
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
    await user.click(screen.getByRole("button", { name: "{{ system.date }}" }));
    expect(body.value).toContain("{{ system.date }}");
  });

  it("publishes a draft version with the optimistic revision", async () => {
    const user = userEvent.setup();
    vi.mocked(api.activateDocumentTemplateVersion).mockResolvedValue(template);
    renderTemplates();
    await screen.findByText("Оффер (базовый)");
    await user.click(screen.getByText("Версии и статусы"));
    await user.click(screen.getByRole("button", { name: "Опубликовать" }));
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
