import { useEffect, useState, useCallback } from "react";
import {
  getCandidateDocuments,
  applyDocumentList,
  updateDocumentItemStatus,
  listDocumentLists,
} from "../../api";
import { Button } from "../../design-system/components/Button";
import { Icon } from "../../design-system/icons/Icon";
import type {
  CandidateDocumentList,
  CandidateDocumentItem,
  DocumentList,
} from "../../types";

interface Props {
  candidateId: string;
}

export function DocumentsTab({ candidateId }: Props) {
  const [docLists, setDocLists] = useState<CandidateDocumentList[]>([]);
  const [availableLists, setAvailableLists] = useState<DocumentList[]>([]);
  const [missingTotal, setMissingTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showApply, setShowApply] = useState(false);
  const [retrying, setRetrying] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [docsData, listsData] = await Promise.all([
        getCandidateDocuments(candidateId),
        listDocumentLists(),
      ]);
      setDocLists(docsData.items);
      setMissingTotal(docsData.missing_mandatory_total);
      setAvailableLists(listsData.items.filter((dl) => dl.published_version));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Ошибка загрузки");
    } finally {
      setLoading(false);
    }
  }, [candidateId]);

  useEffect(() => {
    void load();
  }, [load]);

  const handleApply = async (listId: string) => {
    try {
      await applyDocumentList(candidateId, listId);
      setShowApply(false);
      void load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Ошибка применения списка");
    }
  };

  const handleToggleItem = async (item: CandidateDocumentItem) => {
    const newStatus = item.status === "missing" ? "received" : "missing";
    try {
      await updateDocumentItemStatus(
        candidateId,
        item.document_list_id,
        item.item_key,
        item.version,
        newStatus,
      );
      void load();
    } catch (e) {
      if (e instanceof Error && e.message.includes("409")) {
        setError("Конфликт версий — обновите страницу и повторите.");
      } else {
        setError(e instanceof Error ? e.message : "Ошибка обновления");
      }
    }
  };

  const handleRetry = () => {
    setRetrying(true);
    void load().finally(() => setRetrying(false));
  };

  if (loading) return <p className="muted">Загрузка документов…</p>;

  // Applied lists already bound
  const appliedListIds = new Set(docLists.map((dl) => dl.document_list_id));
  const unapplied = availableLists.filter((dl) => !appliedListIds.has(dl.id));

  return (
    <div className="documents-tab">
      <div className="tab-toolbar">
        {missingTotal > 0 && (
          <span className="missing-badge" role="status">
            Недостающих обязательных: {missingTotal}
          </span>
        )}
        <Button size="sm" variant="secondary" onClick={handleRetry} disabled={retrying}>
          <Icon name="refresh-cw" size={14} /> Обновить
        </Button>
        {unapplied.length > 0 && (
          <Button size="sm" onClick={() => setShowApply(true)}>
            <Icon name="plus" size={14} /> Применить список
          </Button>
        )}
      </div>

      {error && (
        <div className="error-banner" role="alert">
          {error}
          <button onClick={() => setError(null)}>✕</button>
        </div>
      )}

      {showApply && (
        <div className="modal-backdrop" onClick={() => setShowApply(false)}>
          <div className="modal" onClick={(e) => e.stopPropagation()} role="dialog" aria-label="Применить список">
            <h3>Выберите список документов</h3>
            {unapplied.length === 0 ? (
              <p className="muted">Нет доступных списков.</p>
            ) : (
              <ul className="apply-list-options">
                {unapplied.map((dl) => (
                  <li key={dl.id}>
                    <Button size="sm" onClick={() => void handleApply(dl.id)}>
                      {dl.title}
                      {dl.published_version && ` (v${dl.published_version.version_number})`}
                    </Button>
                  </li>
                ))}
              </ul>
            )}
            <div className="modal-actions">
              <Button variant="secondary" onClick={() => setShowApply(false)}>Отмена</Button>
            </div>
          </div>
        </div>
      )}

      {docLists.length === 0 ? (
        <div className="empty-state">
          <Icon name="table" size={24} />
          <p>К кандидату пока не применён ни один список документов.</p>
        </div>
      ) : (
        docLists.map((dl) => (
          <div key={dl.document_list_id} className="candidate-doc-list">
            <div className="doc-list-header">
              <h4>{dl.document_list_title} <span className="version-badge">v{dl.version_number}</span></h4>
            </div>
            <ul className="doc-items-list">
              {dl.items.map((item) => (
                <li
                  key={item.item_key}
                  className={`doc-item ${item.status}`}
                  tabIndex={0}
                  role="button"
                  aria-label={`${item.title}: ${item.status === "received" ? "Получен" : "Недостающий"}`}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault();
                      void handleToggleItem(item);
                    }
                  }}
                  onClick={() => void handleToggleItem(item)}
                >
                  <span className="doc-icon">
                    {item.status === "received" ? (
                      <Icon name="check" size={16} />
                    ) : (
                      <Icon name="alert-circle" size={16} />
                    )}
                  </span>
                  <span className="doc-title">
                    {item.title}
                    {item.mandatory && <span className="mandatory-dot" title="Обязательный">•</span>}
                  </span>
                  <span className="doc-status">
                    {item.status === "received" ? "Получен" : "Недостающий"}
                  </span>
                </li>
              ))}
            </ul>
          </div>
        ))
      )}
    </div>
  );
}
