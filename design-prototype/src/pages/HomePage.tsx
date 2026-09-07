import { PageHeader } from "../components/ui/PageHeader";
import { Avatar } from "../components/ui/Avatar";
import { StageChip } from "../components/ui/StatusChip";
import { Icon } from "../icons/Icon";
import { useAppState } from "../state/AppState";
import { useRouter } from "../router";
import { CURRENT_DATE, EVENTS, USERS, userById } from "../data/mockData";
import { formatDateTime, formatRelative } from "../utils/format";
import "./homePage.css";

export function HomePage() {
  const { currentUserId, candidates } = useAppState();
  const { navigate } = useRouter();
  const currentUser = userById(currentUserId)!;
  const isManager = currentUser.role === "manager" || currentUser.role === "admin";

  const myCandidates = candidates.filter((c) => c.ownerId === currentUserId && !c.isDeleted);
  const upcoming = EVENTS.filter((e) => e.ownerId === currentUserId && e.status === "planned")
    .sort((a, b) => (a.startsAt < b.startsAt ? -1 : 1))
    .slice(0, 5);

  const attentionNeeded = myCandidates.filter((c) => {
    const days = (CURRENT_DATE.getTime() - new Date(c.lastActivityAt).getTime()) / 86_400_000;
    return days > 6 && c.stage !== "hired" && c.stage !== "rejected" && c.stage !== "fired";
  });

  return (
    <div>
      <PageHeader
        title={`Добрый день, ${currentUser.fullName.split(" ")[1] ?? currentUser.fullName}`}
        description="Обзор вашей работы за сегодня: очередь, ближайшие события и то, что требует внимания."
      />

      <div className="home-grid">
        <section className="home-card home-card-wide" aria-labelledby="home-attention-title">
          <div className="home-card-head">
            <h3 id="home-attention-title">Требует внимания</h3>
            <button type="button" className="home-link" onClick={() => navigate("queue")}>
              Открыть очередь
            </button>
          </div>
          {attentionNeeded.length === 0 ? (
            <p className="home-empty">Отлично! Нет кандидатов без активности дольше 6 дней.</p>
          ) : (
            <ul className="home-attention-list">
              {attentionNeeded.slice(0, 5).map((c) => (
                <li key={c.id}>
                  <Avatar initials={c.initials} color={c.avatarColor} size="sm" />
                  <div className="home-attention-text">
                    <span>{c.fullName}</span>
                    <span className="home-attention-meta">Нет активности {formatRelative(c.lastActivityAt, CURRENT_DATE)}</span>
                  </div>
                  <StageChip stage={c.stage} size="sm" />
                </li>
              ))}
            </ul>
          )}
        </section>

        <section className="home-card" aria-labelledby="home-events-title">
          <div className="home-card-head">
            <h3 id="home-events-title">Ближайшие события</h3>
            <button type="button" className="home-link" onClick={() => navigate("calendar")}>Календарь</button>
          </div>
          {upcoming.length === 0 ? (
            <p className="home-empty">На ближайшее время событий не запланировано.</p>
          ) : (
            <ul className="home-events-list">
              {upcoming.map((ev) => (
                <li key={ev.id}>
                  <Icon name={ev.type === "interview" ? "calendar" : ev.type === "call" ? "phone" : "clock"} size={14} />
                  <div>
                    <span className="home-event-title">{ev.title}</span>
                    <span className="home-event-time">{formatDateTime(ev.startsAt)}</span>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </section>

        <section className="home-card" aria-labelledby="home-queue-title">
          <div className="home-card-head">
            <h3 id="home-queue-title">Моя очередь</h3>
            <span className="home-metric">{myCandidates.length}</span>
          </div>
          <p className="home-empty">Активных кандидатов в работе. Нажмите, чтобы перейти к списку.</p>
          <button type="button" className="home-link" onClick={() => navigate("queue")}>Перейти в очередь →</button>
        </section>

        {isManager && (
          <section className="home-card home-card-wide" aria-labelledby="home-team-title">
            <div className="home-card-head">
              <h3 id="home-team-title">Команда сегодня</h3>
              <button type="button" className="home-link" onClick={() => navigate("analytics")}>Аналитика</button>
            </div>
            <ul className="home-team-list">
              {USERS.filter((u) => u.role === "hr").map((u) => {
                const count = candidates.filter((c) => c.ownerId === u.id && !c.isDeleted).length;
                return (
                  <li key={u.id}>
                    <Avatar initials={u.initials} color={u.avatarColor} size="sm" />
                    <span className="home-team-name">{u.fullName}</span>
                    <span className="home-team-count">{count} в работе</span>
                  </li>
                );
              })}
            </ul>
          </section>
        )}
      </div>
    </div>
  );
}
