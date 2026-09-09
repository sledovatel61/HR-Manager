import { useEffect, useState, useCallback } from "react";
import {
  listDocumentLists,
  createDocumentList,
  updateDocumentListDraft,
  publishDocumentListVersion,
  archiveDocumentListVersion,
} from "../../api";
import { Button } from "../../design-system/components/Button";
import { Icon } from "../../design-system/icons/Icon";
import type { DocumentList, DocumentListItem } from "../../types";

export function DocumentListsPage() {
  const [lists, setLists] = useState<DocumentList[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showCreate, setShowCreate] = useState(false);
  const [createTitle, setCreateTitle] = useState("");
  const [createDesc, setCreateDesc] = useState("");
  const [createItems, setCreateItems] = useState<DocumentListItem[]>([]);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editItems, setEditItems] = useState<DocumentListItem[]>([]);
  const [editKey, setEditKey] = useState("");
  const [editTitle, setEditTitle] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await listDocumentLists();
      setLists(data.items);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Ошибка загрузки");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const handleCreate = async () => {
    if (!createTitle.trim()) return;
    try {
      await createDocumentList({
        title: createTitle.trim(),
        description: createDesc.trim() || null,
        items: createItems,
      });
      setCreateTitle("");
      setCreateDesc("");
      setCreateItems([]);
      setShowCreate(false);
      void load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Ошибка создания");
    }
  };

  const handlePublish = async (listId: string, versionId: string) => {
    try {
      await publishDocumentListVersion(listId, versionId);
      void load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Ошибка публикации");
    }
  };

  const handleArchive = async (listId: string, versionId: string) => {
    try {
      await archiveDocumentListVersion(listId, versionId);
      void load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Ошибка архивирования");
    }
  };

  const handleAddItem = () => {
    if (!editKey.trim() || !editTitle.trim()) return;
    if (editItems.some((i) => i.key === editKey.trim())) return;
    setEditItems([...editItems, { key: editKey.trim(), title: editTitle.trim(), mandatory: true }]);
    setEditKey("");
    setEditTitle("");
  };

  const handleSaveDraft = async (listId: string) => {
    try {
      await updateDocumentListDraft(listId, editItems);
      setEditingId(null);
      void load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Ошибка сохранения");
    }
  };

  const handleAddCreateItem = () => {
    if (!editKey.trim() || !editTitle.trim()) return;
    if (createItems.some((i) => i.key === editKey.trim())) return;
    setCreateItems([...createItems, { key: editKey.trim(), title: editTitle.trim(), mandatory: true }]);
    setEditKey("");
    setEditTitle("");
  };

  if (loading) return <p className="muted">Загрузка…</p>;

  return (
    <div className="document-lists-page">
      <div className="page-toolbar">
        <Button variant="primary" onClick={() => setShowCreate(true)}>
          <Icon name="plus" size={14} /> Новый список
        </Button>
      </div>
      {error && <div className="error-banner" role="alert">{error}<button onClick={() => setError(null)}>✕</button></div>}

      {showCreate && (
        <div className="modal-backdrop" onClick={() => setShowCreate(false)}>
          <div className="modal" onClick={(e) => e.stopPropagation()} role="dialog" aria-label="Создание списка">
            <h3>Новый список документов</h3>
            <label>
              Название
              <input value={createTitle} onChange={(e) => setCreateTitle(e.target.value)} maxLength={200} autoFocus />
            </label>
            <label>
              Описание
              <textarea value={createDesc} onChange={(e) => setCreateDesc(e.target.value)} maxLength={2000} rows={3} />
            </label>
            <h4>Элементы</h4>
            {createItems.map((item) => (
              <div key={item.key} className="item-row">
                <span>{item.title} ({item.key})</span>
                <button onClick={() => setCreateItems(createItems.filter((i) => i.key !== item.key))}>✕</button>
              </div>
            ))}
            <div className="add-item-row">
              <input placeholder="Ключ" value={editKey} onChange={(e) => setEditKey(e.target.value)} maxLength={64} />
              <input placeholder="Название" value={editTitle} onChange={(e) => setEditTitle(e.target.value)} maxLength={200} />
              <Button size="sm" onClick={handleAddCreateItem}>Добавить</Button>
            </div>
            <div className="modal-actions">
              <Button variant="secondary" onClick={() => setShowCreate(false)}>Отмена</Button>
              <Button variant="primary" onClick={() => void handleCreate()}>Создать</Button>
            </div>
          </div>
        </div>
      )}

      {lists.length === 0 ? (
        <div className="empty-state">
          <Icon name="table" size={32} />
          <p>Списки документов пока не созданы.</p>
        </div>
      ) : (
        <div className="document-lists-grid">
          {lists.map((dl) => (
            <div key={dl.id} className="document-list-card">
              <div className="card-header">
                <h3>{dl.title}</h3>
                {dl.published_version && <span className="status-chip published">Опубликован v{dl.published_version.version_number}</span>}
                {dl.draft_version && !dl.published_version && <span className="status-chip draft">Черновик v{dl.draft_version.version_number}</span>}
              </div>
              {dl.description && <p className="card-desc">{dl.description}</p>}

              {editingId === dl.id && dl.draft_version ? (
                <div className="edit-items">
                  <h4>Элементы (v{dl.draft_version.version_number})</h4>
                  {editItems.map((item) => (
                    <div key={item.key} className="item-row">
                      <span>{item.title} ({item.key}) {item.mandatory ? "•" : ""}</span>
                      <button onClick={() => setEditItems(editItems.filter((i) => i.key !== item.key))}>✕</button>
                    </div>
                  ))}
                  <div className="add-item-row">
                    <input placeholder="Ключ" value={editKey} onChange={(e) => setEditKey(e.target.value)} />
                    <input placeholder="Название" value={editTitle} onChange={(e) => setEditTitle(e.target.value)} />
                    <Button size="sm" onClick={handleAddItem}>Добавить</Button>
                  </div>
                  <div className="card-actions">
                    <Button size="sm" variant="secondary" onClick={() => setEditingId(null)}>Отмена</Button>
                    <Button size="sm" onClick={() => void handleSaveDraft(dl.id)}>Сохранить</Button>
                  </div>
                </div>
              ) : (
                <>
                  {dl.draft_version && (
                    <div className="card-actions">
                      <Button size="sm" onClick={() => { setEditingId(dl.id); setEditItems(dl.draft_version!.items); }}>
                        Редактировать
                      </Button>
                      <Button size="sm" variant="primary" onClick={() => void handlePublish(dl.id, dl.draft_version!.id)}>
                        Опубликовать
                      </Button>
                    </div>
                  )}
                  {dl.published_version && (
                    <div className="card-actions">
                      <Button size="sm" variant="secondary" onClick={() => void handleArchive(dl.id, dl.published_version!.id)}>
                        Архивировать
                      </Button>
                    </div>
                  )}
                </>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
