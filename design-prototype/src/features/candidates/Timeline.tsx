import { Icon, type IconName } from "../../icons/Icon";
import { userById } from "../../data/mockData";
import { STAGE_LABELS, type Interaction } from "../../types";
import { formatDateTime } from "../../utils/format";
import "./timeline.css";

const TYPE_ICON: Record<Interaction["type"], IconName> = {
  call: "phone",
  email: "mail",
  note: "file-text",
  status_change: "check-circle",
  transfer: "arrow-right-left",
  meeting: "users",
};

export function Timeline({ interactions }: { interactions: Interaction[] }) {
  if (interactions.length === 0) {
    return <p className="timeline-empty">История взаимодействий пуста — добавьте первую запись.</p>;
  }

  return (
    <ol className="timeline" aria-label="История взаимодействий">
      {interactions.map((item) => {
        const author = userById(item.authorId);
        return (
          <li key={item.id} className="timeline-item">
            <span className="timeline-icon">
              <Icon name={TYPE_ICON[item.type]} size={13} />
            </span>
            <div className="timeline-content">
              <div className="timeline-head">
                <span className="timeline-summary">{item.summary}</span>
                <time className="timeline-time" dateTime={item.createdAt}>{formatDateTime(item.createdAt)}</time>
              </div>
              {item.detail && <p className="timeline-detail">{item.detail}</p>}
              {item.type === "status_change" && item.fromStage && item.toStage && (
                <p className="timeline-detail">
                  {STAGE_LABELS[item.fromStage]} → <strong>{STAGE_LABELS[item.toStage]}</strong>
                </p>
              )}
              {item.type === "transfer" && item.toOwnerId && (
                <p className="timeline-detail">
                  Новый ответственный: <strong>{userById(item.toOwnerId)?.fullName}</strong>
                </p>
              )}
              {author && <p className="timeline-author">{author.fullName}</p>}
            </div>
          </li>
        );
      })}
    </ol>
  );
}
