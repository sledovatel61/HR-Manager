import { useCallback, useRef, useState } from "react";
import { documentRequest as request } from "../../api";
import type {
  CandidateStage,
  CandidateMessagePreview,
  CandidateMessageSendResult,
} from "../../types";
import type { CandidateDocuments, DocumentLists } from "./types";
import { LoadState } from "./shared";
import { errorText, useResource } from "./hooks";
import "./documents.css";
export function DocumentsTab({
  candidateId,
  stage,
}: {
  candidateId: string;
  stage: CandidateStage;
}) {
  const load = useCallback(async () => {
    const [documents, lists] = await Promise.all([
      request<CandidateDocuments>(`/candidates/${candidateId}/documents`),
      request<DocumentLists>("/document-lists"),
    ]);
    return { documents, lists };
  }, [candidateId]);
  const resource = useResource(load);
  const [version, setVersion] = useState("");
  const [channel, setChannel] = useState("email");
  const [kind, setKind] = useState("document_request");
  const [preview, setPreview] = useState<CandidateMessagePreview | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const idempotency = useRef({ signature: "", key: "" });
  const docs = resource.data?.documents;
  const mutate = async (action: () => Promise<unknown>, reload = true) => {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await action();
      if (reload) {
        setPreview(null);
        await resource.reload();
      }
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  };
  const payload = {
    message_type: kind,
    channel,
    document_set_id: docs?.set_id,
  };
  const send = async () => {
    const signature = JSON.stringify(payload);
    if (idempotency.current.signature !== signature)
      idempotency.current = { signature, key: crypto.randomUUID() };
    await request<CandidateMessageSendResult>(
      `/candidates/${candidateId}/messages/send`,
      {
        method: "POST",
        body: { ...payload, idempotency_key: idempotency.current.key },
      },
    );
    setNotice(
      "Сообщение поставлено в очередь. История и отмена — во вкладке «Сообщения».",
    );
  };
  return (
    <section className="document-panel">
      <h3>Документы кандидата</h3>
      <p>
        Только факт получения. Файлы, номера документов и заметки здесь не
        хранятся.
      </p>
      <LoadState {...resource} />
      <button
        onClick={() => {
          setPreview(null);
          void resource.reload();
        }}
      >
        Обновить документы
      </button>
      {error && <p role="alert">{error}</p>}
      {notice && <p role="status">{notice}</p>}
      {!resource.loading && !resource.error && docs && (
        <>
          {docs.exact_version ? (
            <p>
              Точная версия: {docs.exact_version.name} · №
              {docs.exact_version.number} · ревизия применения {docs.revision}
            </p>
          ) : (
            <p>Список ещё не применён.</p>
          )}
          <div className="document-form">
            <label>
              Опубликованная версия
              <select
                value={version}
                onChange={(e) => setVersion(e.target.value)}
              >
                <option value="">Выберите список</option>
                {resource.data?.lists.items.flatMap((l) =>
                  l.versions
                    .filter(
                      (v) =>
                        v.state === "published" &&
                        (!v.stage || v.stage === stage),
                    )
                    .map((v) => (
                      <option key={v.id} value={v.id}>
                        {v.name} — версия {v.number}
                      </option>
                    )),
                )}
              </select>
            </label>
            <button
              disabled={busy || !version}
              onClick={() =>
                void mutate(() =>
                  request(`/candidates/${candidateId}/documents`, {
                    method: "POST",
                    body: {
                      version_id: version,
                      expected_revision: docs.revision,
                    },
                  }),
                )
              }
            >
              {docs.set_id
                ? "Заменить список новой версией"
                : "Применить список"}
            </button>
          </div>
          {docs.set_id && (
            <>
              <table className="document-table">
                <thead>
                  <tr>
                    <th>Документ</th>
                    <th>Получение</th>
                  </tr>
                </thead>
                <tbody>
                  {docs.items.map((item) => (
                    <tr key={item.key}>
                      <td>
                        {item.name}
                        {item.required && " (обязательный)"}
                        <p>{item.explanation}</p>
                      </td>
                      <td>
                        <label>
                          <input
                            type="checkbox"
                            aria-label={`Получен: ${item.name}`}
                            checked={item.state === "received"}
                            disabled={busy}
                            onChange={(e) =>
                              void mutate(() =>
                                request(
                                  `/candidates/${candidateId}/documents/${item.key}`,
                                  {
                                    method: "PATCH",
                                    body: {
                                      set_id: docs.set_id,
                                      expected_version: item.version,
                                      state: e.target.checked
                                        ? "received"
                                        : "missing",
                                    },
                                  },
                                ),
                              )
                            }
                          />{" "}
                          Получен
                        </label>
                        <p>
                          Изменено:{" "}
                          {new Date(item.changed_at).toLocaleString("ru")}
                        </p>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {docs.missing_required.length === 0 ? (
                <p>Все обязательные документы получены. Отправка не нужна.</p>
              ) : (
                <div className="document-form">
                  <h4>Запросить недостающие документы</h4>
                  <label>
                    Тип сообщения
                    <select
                      value={kind}
                      onChange={(e) => {
                        setKind(e.target.value);
                        setPreview(null);
                      }}
                    >
                      <option value="document_request">
                        Запрос документов
                      </option>
                      <option value="document_reminder">
                        Напоминание о документах
                      </option>
                    </select>
                  </label>
                  <label>
                    Канал
                    <select
                      value={channel}
                      onChange={(e) => {
                        setChannel(e.target.value);
                        setPreview(null);
                      }}
                    >
                      <option value="email">Электронная почта</option>
                      <option value="telegram">Telegram</option>
                    </select>
                  </label>
                  <p>
                    Канал требует действующего согласия кандидата. Настройка
                    согласий — во вкладке «Сообщения».
                  </p>
                  <button
                    disabled={busy}
                    onClick={() =>
                      void mutate(
                        async () =>
                          setPreview(
                            await request<CandidateMessagePreview>(
                              `/candidates/${candidateId}/messages/preview`,
                              { method: "POST", body: payload },
                            ),
                          ),
                        false,
                      )
                    }
                  >
                    Предпросмотр запроса
                  </button>
                  {preview && (
                    <>
                      <pre>{preview.body}</pre>
                      {!preview.channels.includes(
                        channel as "email" | "telegram",
                      ) && (
                        <p role="alert">
                          Канал не разрешён. Необходимо согласие и подключение.
                        </p>
                      )}
                      <button
                        disabled={
                          busy ||
                          !preview.channels.includes(
                            channel as "email" | "telegram",
                          )
                        }
                        onClick={() => void mutate(send, false)}
                      >
                        Поставить в очередь
                      </button>
                    </>
                  )}
                  <p>
                    «Принято провайдером» не означает доставку или прочтение.
                  </p>
                </div>
              )}
            </>
          )}
        </>
      )}
    </section>
  );
}
