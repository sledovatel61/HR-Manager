import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  downloadGeneratedDocument,
  generateCandidateDocument,
  listDocumentTemplates,
  listGeneratedDocuments,
  previewCandidateDocument,
} from "../../api";
import { Button } from "../../design-system/components/Button";
import { ConfirmDialog } from "../../design-system/components/ConfirmDialog";
import { useToast } from "../../design-system/components/ToastContext";
import type {
  DocumentRenderPreview,
  DocumentTemplate,
  GeneratedDocument,
} from "../../types";
import { errorText } from "../documents/hooks";
import "./documentTemplates.css";

/**
 * Candidate card tab: render a published template version for this candidate,
 * inspect the preview, save an immutable snapshot and download it. Nothing is
 * ever sent to the candidate and no file is stored on the server.
 *
 * Library integration (ветка «Библиотека HR»): the picker lists the same
 * published templates the library shows, ordered so the ones scoped to the
 * candidate's current funnel stage come first, and links to the library for
 * reading questionnaires without generating anything. Opening the library
 * never changes the candidate card and never sends the candidate a message —
 * generating a document stays a separate, confirmed action.
 */
export function GeneratedDocumentsTab({
  candidateId,
  candidateStage,
}: {
  candidateId: string;
  candidateStage?: string;
}) {
  const { pushToast } = useToast();
  const [templates, setTemplates] = useState<DocumentTemplate[] | null>(null);
  const [generations, setGenerations] = useState<GeneratedDocument[]>([]);
  const [versionId, setVersionId] = useState("");
  const [preview, setPreview] = useState<DocumentRenderPreview | null>(null);
  const [busy, setBusy] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const idempotency = useRef({ signature: "", key: "" });

  const load = useCallback(async () => {
    try {
      const [list, page] = await Promise.all([
        listDocumentTemplates(),
        listGeneratedDocuments(candidateId, { limit: 20 }),
      ]);
      setTemplates(list.items);
      setGenerations(page.items);
    } catch (error) {
      pushToast("danger", errorText(error));
    }
  }, [candidateId, pushToast]);

  useEffect(() => {
    void load();
  }, [load]);

  const versions = useMemo(() => {
    const all = (templates ?? []).flatMap((template) =>
      template.versions
        .filter((version) => version.state === "active")
        .map((version) => ({ template, version })),
    );
    // Материалы по текущему этапу кандидата — первыми: так вопросник по
    // этапу находится без чтения названий всех шаблонов.
    const matches = (scope: string) => !scope || scope === candidateStage;
    return all.sort((a, b) => {
      const aMatch = matches(a.template.scope) ? 0 : 1;
      const bMatch = matches(b.template.scope) ? 0 : 1;
      if (aMatch !== bMatch) return aMatch - bMatch;
      return a.template.name.localeCompare(b.template.name, "ru");
    });
  }, [candidateStage, templates]);

  const run = async (action: () => Promise<unknown>, notice: string, reload = true) => {
    setBusy(true);
    try {
      await action();
      if (notice) pushToast("success", notice);
      if (reload) await load();
    } catch (error) {
      pushToast("danger", errorText(error));
    } finally {
      setBusy(false);
    }
  };

  const save = () => {
    if (!preview) return;
    setConfirming(true);
  };

  const confirmSave = () => {
    setConfirming(false);
    if (!preview) return;
    const signature = `${versionId}`;
    if (idempotency.current.signature !== signature) {
      idempotency.current = { signature, key: crypto.randomUUID() };
    }
    void run(
      async () => {
        await generateCandidateDocument(
          candidateId,
          versionId,
          idempotency.current.key,
        );
        setPreview(null);
      },
      "Документ сохранён (неизменяемый снимок)",
    );
  };

  const download = (generation: GeneratedDocument, format: "html" | "txt") => {
    void run(async () => {
      const { blob, filename } = await downloadGeneratedDocument(
        candidateId,
        generation.id,
        format,
      );
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = filename;
      link.click();
      URL.revokeObjectURL(url);
    }, "");
  };

  return (
    <section className="document-panel templates-page">
      <h3>Документы по шаблону</h3>
      <p>
        Документ собирается из опубликованной версии шаблона. Сохранённый документ
        неизменяем: правка шаблона его не меняет. Файлы не хранятся, кандидату ничего
        не отправляется.
      </p>
      <div className="template-actions">
        <Button size="sm" onClick={() => void run(load, "")}>
          Обновить
        </Button>
        <Button
          size="sm"
          variant="ghost"
          icon="book"
          onClick={() => {
            window.location.hash = "#/templates";
          }}
          title="Открыть библиотеку материалов: вопросники и памятки без создания документа"
        >
          Открыть библиотеку
        </Button>
      </div>

      {templates && versions.length === 0 && (
        <p>Нет опубликованных материалов. Опубликуйте версию в разделе «Шаблоны и материалы».</p>
      )}

      {versions.length > 0 && (
        <div className="template-form">
          <label>
            Опубликованная версия материала
            <select
              value={versionId}
              onChange={(event) => {
                setVersionId(event.target.value);
                setPreview(null);
              }}
            >
              <option value="">Выберите материал</option>
              {versions.map(({ template, version }) => (
                <option key={version.id} value={version.id}>
                  {template.name} — версия {version.number}
                  {template.scope && template.scope === candidateStage
                    ? " (по этапу кандидата)"
                    : ""}
                </option>
              ))}
            </select>
          </label>
          <div className="template-actions">
            <Button
              disabled={busy || !versionId}
              onClick={() =>
                void run(async () => {
                  setPreview(await previewCandidateDocument(candidateId, versionId));
                }, "", false)
              }
            >
              Предпросмотр
            </Button>
          </div>
          {preview && (
            <>
              <h4>{preview.title}</h4>
              <pre className="template-body">{preview.body_text}</pre>
              <p className="template-meta">
                Плейсхолдеры: {preview.placeholders.join(", ") || "—"}
              </p>
              <div className="template-actions">
                <Button variant="primary" disabled={busy} onClick={save}>
                  Сохранить документ
                </Button>
                <Button disabled={busy} onClick={() => setPreview(null)}>
                  Отмена
                </Button>
              </div>
            </>
          )}
        </div>
      )}

      <table className="templates-table">
        <thead>
          <tr>
            <th>Документ</th>
            <th>Ревизия</th>
            <th>Создан</th>
            <th>Скачать</th>
          </tr>
        </thead>
        <tbody>
          {generations.length === 0 && (
            <tr>
              <td colSpan={4}>Сохранённых документов пока нет.</td>
            </tr>
          )}
          {generations.map((generation) => (
            <tr key={generation.id}>
              <td>
                {generation.template_name} · v{generation.template_number} ·{" "}
                {generation.template_title}
                <p className="template-meta">sha256: {generation.content_sha256.slice(0, 16)}…</p>
              </td>
              <td>{generation.revision}</td>
              <td>{new Date(generation.created_at).toLocaleString("ru")}</td>
              <td className="template-actions">
                <Button
                  size="sm"
                  icon="download"
                  disabled={busy}
                  onClick={() => download(generation, "html")}
                >
                  HTML
                </Button>
                <Button
                  size="sm"
                  disabled={busy}
                  onClick={() => download(generation, "txt")}
                >
                  Текст
                </Button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="template-meta">
        HTML открывается в браузере и печатается в PDF средствами браузера. Серверная
        генерация PDF/DOCX в этой версии не поддерживается.
      </p>

      {confirming && (
        <ConfirmDialog
          open
          title="Сохранить документ для этого кандидата?"
          description="Документ создаётся как неизменяемый снимок выбранной версии и остаётся в истории карточки. Кандидату ничего не отправляется."
          confirmLabel="Сохранить документ"
          onCancel={() => setConfirming(false)}
          onConfirm={confirmSave}
        />
      )}
    </section>
  );
}
