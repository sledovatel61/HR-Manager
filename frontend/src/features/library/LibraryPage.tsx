import { useCallback, useEffect, useMemo, useState } from "react";
import {
  ApiError,
  downloadLibraryMaterial,
  getLibraryMaterial,
  libraryMaterialViewUrl,
  listLibraryMaterials,
} from "../../api";
import { Button } from "../../design-system/components/Button";
import { Field, TextInput } from "../../design-system/components/Field";
import {
  EmptyState,
  ErrorState,
  SkeletonRows,
} from "../../design-system/components/StateViews";
import { Badge } from "../../design-system/components/StatusChip";
import { useToast } from "../../design-system/components/ToastContext";
import { Icon } from "../../design-system/icons/Icon";
import type {
  LibraryMaterial,
  LibraryMaterialDetail,
  LibraryMaterials,
} from "../../types";
import { errorText } from "../documents/hooks";
import { STAGE_LABELS, type CandidateStage } from "../../types";
import { kindLabel as kindLabelOf } from "../document-templates/vocabulary";
import {
  countByCategory,
  libraryCategoryLabel,
  mergeCategories,
} from "./libraryCategories";
import { ManageMaterialsPanel } from "./ManageMaterialsPanel";
import "./library.css";

/** Local "recently opened" ids — no personal data, only material ids. */
const RECENT_KEY = "hrm-library-recent";
const RECENT_LIMIT = 4;

function readRecent(): string[] {
  try {
    const raw = window.localStorage.getItem(RECENT_KEY);
    const parsed: unknown = raw ? JSON.parse(raw) : [];
    if (!Array.isArray(parsed)) return [];
    return parsed.filter((item): item is string => typeof item === "string").slice(0, RECENT_LIMIT);
  } catch {
    return [];
  }
}

function writeRecent(ids: readonly string[]): void {
  try {
    window.localStorage.setItem(RECENT_KEY, JSON.stringify(ids.slice(0, RECENT_LIMIT)));
  } catch {
    // localStorage may be unavailable (private mode) — «Недавно открытые» is
    // a convenience, never a requirement.
  }
}

function formatDate(value: string | null): string {
  if (!value) return "";
  return new Date(value).toLocaleDateString("ru-RU");
}

function scopeLabel(scope: string): string {
  if (!scope) return "Все этапы воронки";
  return STAGE_LABELS[scope as CandidateStage] ?? scope;
}

/**
 * «Библиотека HR» — the read-first screen of ready-made materials.
 *
 * The regular HR opens the section, finds a questionnaire or memo in a
 * couple of seconds, reads, prints or downloads it. Creating and editing
 * content lives behind the secondary «Управление материалами» mode and is
 * only reachable with the manage right (the server re-checks every call).
 */
export function LibraryPage() {
  const { pushToast } = useToast();
  const [library, setLibrary] = useState<LibraryMaterials | null>(null);
  const [loadError, setLoadError] = useState("");
  const [search, setSearch] = useState("");
  const [category, setCategory] = useState("");
  const [detail, setDetail] = useState<LibraryMaterialDetail | null>(null);
  const [detailError, setDetailError] = useState("");
  const [manageMode, setManageMode] = useState(false);
  const [recentIds, setRecentIds] = useState<string[]>(readRecent());

  const load = useCallback(async () => {
    setLoadError("");
    try {
      const data = await listLibraryMaterials();
      setLibrary(data);
    } catch (error) {
      setLoadError(errorText(error));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const items = useMemo(() => library?.items ?? [], [library]);
  const categories = useMemo(() => mergeCategories(library?.categories ?? []), [library]);
  const counts = useMemo(() => countByCategory(items), [items]);

  const visible = useMemo(() => {
    const query = search.trim().toLowerCase();
    return items.filter((material) => {
      if (category && material.category !== category) return false;
      if (!query) return true;
      const haystack = [
        material.name,
        material.summary,
        material.title,
        material.kind,
        kindLabelOf(material.kind),
        libraryCategoryLabel(material.category),
        scopeLabel(material.scope),
      ]
        .join(" ")
        .toLowerCase();
      return haystack.includes(query);
    });
  }, [category, items, search]);

  const recent = useMemo(() => {
    const byId = new Map(items.map((material) => [material.id, material]));
    return recentIds
      .map((id) => byId.get(id))
      .filter((material): material is LibraryMaterial => Boolean(material));
  }, [items, recentIds]);

  const openMaterial = useCallback(
    async (id: string) => {
      setDetailError("");
      try {
        const data = await getLibraryMaterial(id);
        setDetail(data);
        setRecentIds((current) => {
          const next = [id, ...current.filter((item) => item !== id)];
          writeRecent(next);
          return next;
        });
      } catch (error) {
        setDetailError(errorText(error));
      }
    },
    [],
  );

  const download = async (id: string, format: "html" | "txt") => {
    try {
      const { blob, filename } = await downloadLibraryMaterial(id, format);
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = filename;
      link.click();
      URL.revokeObjectURL(url);
    } catch (error) {
      pushToast(
        "danger",
        error instanceof ApiError ? error.message : errorText(error),
      );
    }
  };

  const print = () => {
    // The print area is part of this screen and filled from the safe API
    // response; @media print CSS hides the rest of the shell.
    const body = document.body;
    body.classList.add("library-printing");
    const cleanup = () => {
      body.classList.remove("library-printing");
      window.removeEventListener("afterprint", cleanup);
    };
    window.addEventListener("afterprint", cleanup);
    try {
      window.print();
    } catch {
      // jsdom/non-browser environments — ignore silently
    }
    // Fallback cleanup if afterprint never fires (some browsers + cancel).
    window.setTimeout(() => body.classList.remove("library-printing"), 2000);
  };

  const openInNewWindow = (id: string) => {
    window.open(libraryMaterialViewUrl(id), "_blank", "noopener");
  };

  if (manageMode) {
    return (
      <ManageMaterialsPanel
        onBack={() => {
          setManageMode(false);
          void load();
        }}
      />
    );
  }

  const filtersActive = search.trim() !== "" || category !== "";

  return (
    <section className="templates-page library-page" aria-label="Библиотека материалов">
      <header className="library-header">
        <div>
          <h1 className="library-title">
            <Icon name="book" size={22} />
            Библиотека HR
          </h1>
          <p className="library-subtitle">
            Готовые материалы для подбора, собеседования, оформления и адаптации:
            вопросники, чек-листы, скрипты и памятки. Откройте, прочитайте,
            распечатайте или скачайте копию.
          </p>
        </div>
        {library?.can_manage && (
          <Button
            variant="secondary"
            icon="edit"
            onClick={() => setManageMode(true)}
          >
            Управление материалами
          </Button>
        )}
      </header>

      {detailError && (
        <div role="alert" className="template-form-error">
          {detailError}{" "}
          <Button size="sm" variant="secondary" onClick={() => void load()}>
            Обновить
          </Button>
        </div>
      )}

      {!library && !loadError && <SkeletonRows rows={4} columns={3} />}
      {!library && loadError && <ErrorState onRetry={() => void load()} />}

      {library && (
        <>
          <div className="library-toolbar">
            <Field label="Поиск материала">
              {(id) => (
                <div className="library-search">
                  <Icon name="search" size={16} />
                  <TextInput
                    id={id}
                    type="search"
                    placeholder="Найти: собеседование, документы, звонок…"
                    value={search}
                    onChange={(event) => setSearch(event.target.value)}
                  />
                </div>
              )}
            </Field>
            <div className="library-chips" role="group" aria-label="Категории материалов">
              <button
                type="button"
                className={`library-chip ${category === "" ? "is-active" : ""}`}
                aria-pressed={category === ""}
                onClick={() => setCategory("")}
              >
                Все <span className="library-chip-count">{items.length}</span>
              </button>
              {categories.map((option) => (
                <button
                  key={option.value}
                  type="button"
                  className={`library-chip ${category === option.value ? "is-active" : ""}`}
                  aria-pressed={category === option.value}
                  onClick={() => setCategory(option.value)}
                >
                  {option.label}{" "}
                  <span className="library-chip-count">{counts.get(option.value) ?? 0}</span>
                </button>
              ))}
            </div>
          </div>

          {filtersActive && (
            <p className="template-meta templates-count" role="status">
              Показано {visible.length} из {items.length}
            </p>
          )}

          {recent.length > 0 && !filtersActive && (
            <div className="library-recent">
              <span className="template-meta">Недавно открытые:</span>
              {recent.map((material) => (
                <button
                  key={material.id}
                  type="button"
                  className="library-recent-link"
                  onClick={() => void openMaterial(material.id)}
                >
                  {material.name}
                </button>
              ))}
            </div>
          )}

          {items.length === 0 && (
            <EmptyState
              icon="book"
              title="В библиотеке пока нет материалов"
              description="Готовые вопросники, чек-листы и памятки появятся здесь после публикации. Нажмите «Обновить» или загляните позже."
              action={
                <Button variant="secondary" icon="loader" onClick={() => void load()}>
                  Обновить
                </Button>
              }
            />
          )}

          {items.length > 0 && visible.length === 0 && (
            <EmptyState
              icon="search"
              title="Ничего не найдено"
              description="По запросу нет совпадений. Измените текст поиска или выберите другую категорию."
              action={
                <Button
                  variant="secondary"
                  onClick={() => {
                    setSearch("");
                    setCategory("");
                  }}
                >
                  Сбросить поиск
                </Button>
              }
            />
          )}

          <div className="library-grid">
            {visible.map((material) => (
              <article key={material.id} className="library-card">
                <div className="library-card-body">
                  <h2 className="library-card-title">
                    <button
                      type="button"
                      className="library-card-open"
                      onClick={() => void openMaterial(material.id)}
                    >
                      {material.name}
                    </button>
                  </h2>
                  <p className="library-card-summary">
                    {material.summary || "Материал для работы HR."}
                  </p>
                  <p className="library-card-meta">
                    <Badge tone="neutral">{libraryCategoryLabel(material.category)}</Badge>
                    <span>{kindLabelOf(material.kind)}</span>
                    <span aria-hidden="true">·</span>
                    <span>Текст / HTML</span>
                    <span aria-hidden="true">·</span>
                    <span>{scopeLabel(material.scope)}</span>
                  </p>
                </div>
                <div className="library-card-actions">
                  <Button
                    size="sm"
                    variant="primary"
                    onClick={() => void openMaterial(material.id)}
                  >
                    Открыть
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    icon="download"
                    title="Скачать копию (HTML)"
                    aria-label={`Скачать копию материала «${material.name}»`}
                    onClick={() => void download(material.id, "html")}
                  >
                    Скачать
                  </Button>
                </div>
              </article>
            ))}
          </div>
        </>
      )}

      {detail && (
        <MaterialView
          material={detail}
          onBack={() => setDetail(null)}
          onDownload={(format) => void download(detail.id, format)}
          onPrint={print}
          onOpenNewWindow={() => openInNewWindow(detail.id)}
        />
      )}
    </section>
  );
}

function MaterialView({
  material,
  onBack,
  onDownload,
  onPrint,
  onOpenNewWindow,
}: {
  material: LibraryMaterialDetail;
  onBack: () => void;
  onDownload: (format: "html" | "txt") => void;
  onPrint: () => void;
  onOpenNewWindow: () => void;
}) {
  return (
    <section className="library-view" aria-label={`Материал: ${material.name}`}>
      <div className="library-view-actions">
        <Button variant="ghost" icon="chevron-left" onClick={onBack}>
          Назад в библиотеку
        </Button>
        <div className="library-view-tools">
          <Button size="sm" variant="secondary" icon="print" onClick={onPrint}>
            Распечатать
          </Button>
          <Button
            size="sm"
            variant="secondary"
            icon="download"
            onClick={() => onDownload("html")}
          >
            Скачать копию
          </Button>
          <Button size="sm" variant="ghost" onClick={() => onDownload("txt")}>
            Текст (.txt)
          </Button>
          <Button
            size="sm"
            variant="ghost"
            icon="eye"
            onClick={onOpenNewWindow}
            title="Открыть материал в новой вкладке браузера"
          >
            В новом окне
          </Button>
        </div>
      </div>

      <article className="library-view-document">
        <h2 className="library-view-title">{material.title}</h2>
        <p className="template-meta">
          Категория: {libraryCategoryLabel(material.category)} ·{" "}
          {kindLabelOf(material.kind)} · версия {material.version_number} ·
          опубликовано: {formatDate(material.published_at) || "дата не указана"}
        </p>
        {/* Server-rendered, escape-first fragment (same pipeline as generated
            documents); the SPA only displays it. The fragment never contains
            scripts (see app.template_render), and the page forbids them. */}
        <div
          className="library-view-content"
          dangerouslySetInnerHTML={{ __html: material.body_html }}
        />
        {material.has_placeholders && (
          <aside className="library-view-note" role="note">
            <h3>Этот материал использует подстановки</h3>
            <p>
              В тексте показаны примеры значений. Настоящие данные подставятся
              автоматически при создании документа в карточке кандидата.
            </p>
            <ul>
              {material.placeholder_hints.map((hint) => (
                <li key={hint.token}>{hint.hint}</li>
              ))}
            </ul>
          </aside>
        )}
      </article>

      {/* Print-only copy: hidden on screen, visible in @media print. */}
      <div className="library-print-area" aria-hidden="true">
        <h1>{material.title}</h1>
        <div dangerouslySetInnerHTML={{ __html: material.body_html }} />
      </div>
    </section>
  );
}
