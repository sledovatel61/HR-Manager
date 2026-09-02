import { useMemo, useState } from "react";
import { PageHeader } from "../components/ui/PageHeader";
import { IconButton } from "../components/ui/Button";
import { Icon } from "../icons/Icon";
import { StageChip } from "../components/ui/StatusChip";
import { useAppState } from "../state/AppState";
import { CURRENT_DATE, candidateById } from "../data/mockData";
import { formatDate } from "../utils/format";
import "./calendarPage.css";

const HOURS = Array.from({ length: 10 }, (_, i) => 8 + i); // 8:00–17:00

function startOfWeek(date: Date): Date {
  const d = new Date(date);
  const day = (d.getDay() + 6) % 7; // Monday = 0
  d.setDate(d.getDate() - day);
  d.setHours(0, 0, 0, 0);
  return d;
}

export function CalendarPage() {
  const { events, pushToast } = useAppState();
  const [weekOffset, setWeekOffset] = useState(0);

  const weekStart = useMemo(() => {
    const s = startOfWeek(CURRENT_DATE);
    s.setDate(s.getDate() + weekOffset * 7);
    return s;
  }, [weekOffset]);

  const days = useMemo(
    () =>
      Array.from({ length: 5 }, (_, i) => {
        const d = new Date(weekStart);
        d.setDate(d.getDate() + i);
        return d;
      }),
    [weekStart],
  );

  const weekEvents = events.filter((e) => {
    const d = new Date(e.startsAt);
    return d >= weekStart && d < new Date(weekStart.getTime() + 5 * 86_400_000 + 86_400_000);
  });

  return (
    <div>
      <PageHeader
        title="Календарь"
        description="Звонки, собеседования и напоминания вашей команды на неделю."
        actions={
          <div className="calendar-nav">
            <IconButton icon="chevron-left" label="Предыдущая неделя" onClick={() => setWeekOffset((w) => w - 1)} />
            <span className="calendar-range">
              {formatDate(days[0].toISOString())} — {formatDate(days[4].toISOString())}
            </span>
            <IconButton icon="chevron-right" label="Следующая неделя" onClick={() => setWeekOffset((w) => w + 1)} />
          </div>
        }
      />

      <div className="calendar-grid" role="table" aria-label="Расписание на неделю">
        <div className="calendar-row calendar-head-row" role="row">
          <div className="calendar-time-col" role="columnheader" aria-hidden="true" />
          {days.map((d) => (
            <div key={d.toISOString()} className="calendar-day-head" role="columnheader">
              <span className="calendar-day-name">{d.toLocaleDateString("ru-RU", { weekday: "short" })}</span>
              <span className="calendar-day-num">{d.getDate()}</span>
            </div>
          ))}
        </div>

        {HOURS.map((hour) => (
          <div className="calendar-row" role="row" key={hour}>
            <div className="calendar-time-col" role="rowheader">{hour}:00</div>
            {days.map((d) => {
              const cellEvents = weekEvents.filter((e) => {
                const ev = new Date(e.startsAt);
                return ev.toDateString() === d.toDateString() && ev.getHours() === hour;
              });
              return (
                <div key={d.toISOString() + hour} className="calendar-cell" role="cell">
                  {cellEvents.map((ev) => {
                    const candidate = candidateById(ev.candidateId);
                    return (
                      <button
                        type="button"
                        key={ev.id}
                        className={`calendar-event calendar-event-${ev.type}`}
                        onClick={() => pushToast("info", `${ev.title} — ${candidate?.fullName ?? ""}`)}
                      >
                        <Icon name={ev.type === "interview" ? "calendar" : ev.type === "call" ? "phone" : ev.type === "meeting" ? "users" : "clock"} size={11} />
                        <span className="calendar-event-title">{ev.title}</span>
                        {candidate && <span className="calendar-event-candidate">{candidate.fullName}</span>}
                      </button>
                    );
                  })}
                </div>
              );
            })}
          </div>
        ))}
      </div>

      <section className="calendar-agenda" aria-labelledby="calendar-agenda-title">
        <h3 id="calendar-agenda-title">Список событий недели</h3>
        <ul>
          {weekEvents
            .sort((a, b) => (a.startsAt < b.startsAt ? -1 : 1))
            .map((ev) => {
              const candidate = candidateById(ev.candidateId);
              return (
                <li key={ev.id}>
                  <span className="agenda-time">{new Date(ev.startsAt).toLocaleString("ru-RU", { weekday: "short", hour: "2-digit", minute: "2-digit" })}</span>
                  <span className="agenda-title">{ev.title}{candidate ? ` — ${candidate.fullName}` : ""}</span>
                  {candidate && <StageChip stage={candidate.stage} size="sm" />}
                </li>
              );
            })}
          {weekEvents.length === 0 && <li className="agenda-empty">На этой неделе событий не запланировано.</li>}
        </ul>
      </section>
    </div>
  );
}
