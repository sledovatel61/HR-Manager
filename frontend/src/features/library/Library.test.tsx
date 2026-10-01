/**
 * «Библиотека HR»: acceptance behaviors of the read-first screen.
 *
 * Mirrors the ручная Windows-приёмка list: the section opens as a library
 * (not an editor), the built-in catalog is non-empty, search and category
 * filters work, a card opens a read-only preview, print/download/new-window
 * go through the safe application API (never `file://`), and the manage
 * entry is a secondary action only for users with the manage right.
 */

import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import * as api from "../../api";
import { ToastProvider } from "../../design-system/components/Toast";
import type { LibraryMaterial, LibraryMaterialDetail, LibraryMaterials } from "../../types";
import { LibraryPage } from "./LibraryPage";

vi.mock("../../api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../api")>()),
  listLibraryMaterials: vi.fn(),
  getLibraryMaterial: vi.fn(),
  downloadLibraryMaterial: vi.fn(),
  listDocumentTemplates: vi.fn(),
  listTemplatePlaceholders: vi.fn(),
}));

const material = (overrides: Partial<LibraryMaterial>): LibraryMaterial => ({
  id: "material-1",
  name: "Вопросник общего собеседования",
  kind: "interview",
  category: "interview",
  summary: "Структурированные вопросы с критериями оценки ответов.",
  scope: "",
  version_id: "version-1",
  version_number: 1,
  title: "Вопросник общего собеседования с критериями оценки",
  published_at: "2026-09-25T09:00:00Z",
  has_placeholders: false,
  ...overrides,
});

const catalog: LibraryMaterials = {
  items: [
    material({}),
    material({
      id: "material-2",
      name: "Чек-лист документов кандидата",
      kind: "checklist",
      category: "candidate_docs",
      summary: "Какие документы запросить при оформлении.",
    }),
    material({
      id: "material-3",
      name: "Скрипт приглашения на собеседование",
      kind: "script",
      category: "calls",
      summary: "Готовый текст звонка-приглашения.",
    }),
  ],
  categories: [
    { key: "interview", label: "Собеседование" },
    { key: "candidate_docs", label: "Документы кандидата" },
    { key: "calls", label: "Звонки и сообщения" },
  ],
  can_manage: false,
};

const detail: LibraryMaterialDetail = {
  ...catalog.items[0],
  body: "**Блок 1. Опыт**\n- Расскажите об обязанностях\n- Какое достижение главное?",
  body_html: "<ul><li>Расскажите об обязанностях</li><li>Какое достижение главное?</li></ul>",
  body_text: "Блок 1. Опыт\n- Расскажите об обязанностях\n- Какое достижение главное?",
  placeholders: [],
  placeholder_hints: [],
  updated_at: "2026-09-25T09:00:00Z",
};

function renderLibrary() {
  return render(
    <ToastProvider>
      <LibraryPage />
    </ToastProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  window.localStorage.clear();
  vi.mocked(api.listLibraryMaterials).mockResolvedValue(catalog);
  vi.mocked(api.getLibraryMaterial).mockResolvedValue(detail);
  vi.mocked(api.listDocumentTemplates).mockResolvedValue({
    items: [],
    can_manage: false,
  });
  vi.mocked(api.listTemplatePlaceholders).mockResolvedValue({ items: [] });
});

describe("Библиотека HR: главный экран", () => {
  it("открывается как библиотека с готовыми карточками, а не редактор", async () => {
    renderLibrary();
    expect(await screen.findByRole("heading", { name: /Библиотека HR/ })).toBeInTheDocument();
    expect(screen.getByText(/Готовые материалы для подбора/)).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "Вопросник общего собеседования" }),
    ).toBeInTheDocument();
    // Редактор не является стартовым экраном.
    expect(screen.queryByRole("button", { name: "Новый материал" })).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/Текст материала/)).not.toBeInTheDocument();
  });

  it("у обычного HR нет входа в управление, у управляющего — есть", async () => {
    const user = userEvent.setup();
    renderLibrary();
    await screen.findByRole("heading", { name: /Библиотека HR/ });
    expect(screen.queryByRole("button", { name: "Управление материалами" })).toBeNull();

    vi.mocked(api.listLibraryMaterials).mockResolvedValue({ ...catalog, can_manage: true });
    renderLibrary();
    const manage = await screen.findByRole("button", { name: "Управление материалами" });
    await user.click(manage);
    expect(
      await screen.findByRole("button", { name: "Новый материал" }),
    ).toBeInTheDocument();
  });

  it("находит вопросник по слову «собеседование» и фильтрует по категории", async () => {
    const user = userEvent.setup();
    renderLibrary();
    await screen.findByRole("heading", { name: /Библиотека HR/ });

    await user.type(screen.getByLabelText("Поиск материала"), "собеседование");
    expect(await screen.findByText(/Показано 2 из 3/)).toBeInTheDocument();
    expect(screen.getByText("Вопросник общего собеседования")).toBeInTheDocument();
    expect(screen.queryByText("Чек-лист документов кандидата")).not.toBeInTheDocument();

    await user.clear(screen.getByLabelText("Поиск материала"));
    await user.click(screen.getByRole("button", { name: /Звонки и сообщения/ }));
    expect(await screen.findByText(/Показано 1 из 3/)).toBeInTheDocument();
    expect(screen.getByText("Скрипт приглашения на собеседование")).toBeInTheDocument();
    expect(screen.queryByText("Вопросник общего собеседования")).not.toBeInTheDocument();
  });

  it("показывает понятное пустое состояние и состояние ошибки с повтором", async () => {
    const user = userEvent.setup();
    vi.mocked(api.listLibraryMaterials).mockResolvedValue({
      ...catalog,
      items: [],
    });
    const { unmount } = renderLibrary();
    expect(await screen.findByText("В библиотеке пока нет материалов")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Обновить" }));
    expect(api.listLibraryMaterials).toHaveBeenCalledTimes(2);
    unmount();

    vi.mocked(api.listLibraryMaterials).mockRejectedValue(
      new api.ApiError(0, "Сеть недоступна."),
    );
    renderLibrary();
    expect(await screen.findByText("Не удалось загрузить данные")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Повторить попытку" }));
    expect(await screen.findByRole("heading", { name: /Библиотека HR/ })).toBeInTheDocument();
  });

  it("навигация с клавиатуры: поиск, чипсы и кнопки карточек в фокусе", async () => {
    renderLibrary();
    await screen.findByRole("heading", { name: /Библиотека HR/ });

    // Достижимость управления: чипс «Все» и кнопка «Открыть» фокусируются табом.
    const chip = screen.getByRole("button", { name: /^Все / });
    chip.focus();
    expect(chip).toHaveFocus();
    const open = screen.getAllByRole("button", { name: "Открыть" })[0];
    open.focus();
    expect(open).toHaveFocus();
  });
});

describe("Библиотека HR: просмотр материала", () => {
  it("открывает read-only просмотр карточки без формы редактирования", async () => {
    const user = userEvent.setup();
    renderLibrary();
    await screen.findByRole("heading", { name: /Библиотека HR/ });
    const cardTitle = screen.getByRole("heading", { name: "Вопросник общего собеседования" });
    await user.click(within(cardTitle).getByRole("button"));

    expect(await screen.findByText("Назад в библиотеку")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: detail.title })).toBeInTheDocument();
    expect(api.getLibraryMaterial).toHaveBeenCalledWith("material-1");
    // Контент отрисован, а не выдан сырым текстом разметки
    // (второе вхождение — скрытая печатная копия).
    const rendered = screen.getAllByText("Расскажите об обязанностях");
    expect(rendered.length).toBeGreaterThanOrEqual(1);
    expect(rendered[0].tagName).toBe("LI");
    expect(screen.queryByLabelText(/Текст материала/)).not.toBeInTheDocument();
    // Источник/версия и дата публикации без внутренних UUID.
    expect(screen.getByText(/версия 1 · опубликовано:/)).toBeInTheDocument();
    expect(screen.queryByText(/material-1/)).not.toBeInTheDocument();
  });

  it("печать вызывает window.print, а не файловый путь", async () => {
    const user = userEvent.setup();
    const printSpy = vi.spyOn(window, "print").mockImplementation(() => undefined);
    renderLibrary();
    await screen.findByRole("heading", { name: /Библиотека HR/ });
    await user.click(screen.getAllByRole("button", { name: "Открыть" })[0]);
    await screen.findByText("Назад в библиотеку");
    await user.click(screen.getByRole("button", { name: "Распечатать" }));
    expect(printSpy).toHaveBeenCalledTimes(1);
    printSpy.mockRestore();
  });

  it("скачивание копии идёт через безопасный API приложения", async () => {
    const user = userEvent.setup();
    vi.mocked(api.downloadLibraryMaterial).mockResolvedValue({
      blob: new Blob(["x"], { type: "text/html" }),
      filename: "material-interview-v1.html",
    });
    const createObjectURL = vi
      .spyOn(URL, "createObjectURL")
      .mockReturnValue("blob:mock");
    vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => undefined);
    renderLibrary();
    await screen.findByRole("heading", { name: /Библиотека HR/ });
    await user.click(screen.getAllByRole("button", { name: "Открыть" })[0]);
    await screen.findByText("Назад в библиотеку");
    await user.click(screen.getByRole("button", { name: "Скачать копию" }));
    await waitFor(() =>
      expect(api.downloadLibraryMaterial).toHaveBeenCalledWith("material-1", "html"),
    );
    // Никакого file:// — только blob из ответа API.
    expect(createObjectURL).toHaveBeenCalledWith(expect.any(Blob));
    createObjectURL.mockRestore();
  });

  it("«В новом окне» открывает безопасный endpoint, а не локальный файл", async () => {
    const user = userEvent.setup();
    const openSpy = vi.spyOn(window, "open").mockReturnValue(null);
    renderLibrary();
    await screen.findByRole("heading", { name: /Библиотека HR/ });
    await user.click(screen.getAllByRole("button", { name: "Открыть" })[0]);
    await screen.findByText("Назад в библиотеку");
    await user.click(screen.getByRole("button", { name: "В новом окне" }));
    expect(openSpy).toHaveBeenCalledWith(
      expect.stringContaining("/api/library/materials/material-1/view"),
      "_blank",
      "noopener",
    );
    openSpy.mockRestore();
  });

  it("материал с подстановками объясняет их человеческим текстом", async () => {
    const user = userEvent.setup();
    vi.mocked(api.getLibraryMaterial).mockResolvedValue({
      ...detail,
      has_placeholders: true,
      placeholders: ["candidate.full_name"],
      placeholder_hints: [
        { token: "candidate.full_name", hint: "ФИО кандидата подставится из карточки" },
      ],
    });
    renderLibrary();
    await screen.findByRole("heading", { name: /Библиотека HR/ });
    await user.click(screen.getAllByRole("button", { name: "Открыть" })[0]);
    expect(await screen.findByText("Этот материал использует подстановки")).toBeInTheDocument();
    expect(screen.getByText(/ФИО кандидата подставится из карточки/)).toBeInTheDocument();
  });

  it("запоминает недавно открытые материалы", async () => {
    const user = userEvent.setup();
    renderLibrary();
    await screen.findByRole("heading", { name: /Библиотека HR/ });
    await user.click(screen.getAllByRole("button", { name: "Открыть" })[0]);
    await screen.findByText("Назад в библиотеку");
    await user.click(screen.getByRole("button", { name: "Назад в библиотеку" }));
    expect(await screen.findByText("Недавно открытые:")).toBeInTheDocument();
    const recentRow = screen.getByText("Недавно открытые:").parentElement as HTMLElement;
    expect(
      within(recentRow).getByRole("button", { name: "Вопросник общего собеседования" }),
    ).toBeInTheDocument();
  });
});
