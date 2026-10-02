import { useCallback, useEffect, useMemo, useState } from "react";
import {
  ApiError,
  assignScheduleImportRows,
  exportCurrentScheduleImportXlsx,
  exportWorkScheduleXlsx,
  fetchWorkScheduleSuggestions,
  listActiveScheduleImportRows,
  listHrUsers,
  listWorkSchedule,
  updateCandidate,
  updateScheduleEntry,
} from "../../api";
import { Button, IconButton } from "../../design-system/components/Button";
import { Field, SelectInput, TextInput } from "../../design-system/components/Field";
import { EmptyState, ErrorState, SkeletonRows } from "../../design-system/components/StateViews";
import { Badge } from "../../design-system/components/StatusChip";
import { useToast } from "../../design-system/components/ToastContext";
import { Icon } from "../../design-system/icons/Icon";
import {
  CANDIDATE_STAGE_ORDER,
  STAGE_LABELS,
  type ActiveScheduleImportRows,
  type ActiveScheduleImportRow,
  type CandidateStage,
  type ScheduleRowKind,
  type User,
  type UserListItem,
  type WorkScheduleList,
  type WorkScheduleQuery,
  type WorkScheduleRow,
  type WorkScheduleSuggestions,
} from "../../types";
import { ScheduleEntryModal } from "./ScheduleEntryModal";
import { ScheduleImportDialog } from "./ScheduleImportDialog";
import {
  addDays,
  displayTime,
  endOfMonth,
  formatDayHeading,
  formatPeriodLabel,
  startOfMonth,
  startOfWeek,
  toIsoDate,
  today,
} from "./scheduleDate";
import "./schedule.css";
import "./print.css";

type PeriodPreset = "two_weeks" | "this_week" | "next_week" | "month" | "custom";

interface SchedulePageProps {
  user: User;
  /** Клик по ФИО открывает карточку кандидата (переход делает Workspace). */
  onOpenCandidate: (id: string) => void;
}

interface Period {
  from: string;
  to: string;
  preset: PeriodPreset;
}

interface RowEdit {
  id: string;
  kind: ScheduleRowKind;
  date: string;
  time: string;
}

function presetRange(preset: Exclude<PeriodPreset, "custom">): Period {
  const base = today();
  if (preset === "this_week") {
    const start = startOfWeek(base);
    return { from: toIsoDate(start), to: toIsoDate(addDays(start, 6)), preset };
  }
  if (preset === "next_week") {
    const start = addDays(startOfWeek(base), 7);
    return { from: toIsoDate(start), to: toIsoDate(addDays(start, 6)), preset };
  }
  if (preset === "month") {
    return { from: toIsoDate(startOfMonth(base)), to: toIsoDate(endOfMonth(base)), preset };
  }
  // По умолчанию: текущая неделя и следующая (график выходов смотрят вперёд).
  const start = startOfWeek(base);
  return { from: toIsoDate(start), to: toIsoDate(addDays(start, 13)), preset: "two_weeks" };
}

/**
 * Права на правку строки: кандидатов правит любой, кто видит график;
 * служебную строку — автор (или тот, кто видит всех кандидатов: руководитель
 * и администратор — сервер решает окончательно и отвечает 403).
 */
function rowIsEditable(row: WorkScheduleRow, user: User): boolean {
  if (row.kind !== "entry") return true;
  if (user.role !== "hr") return true;
  return row.owner_user_id === user.id;
}

function groupByDay(items: WorkScheduleRow[]) {
  const days = new Map<string, WorkScheduleRow[]>();
  for (const item of items) {
    const bucket = days.get(item.entry_date);
    if (bucket) {
      bucket.push(item);
    } else {
      days.set(item.entry_date, [item]);
    }
  }
  return [...days.entries()].map(([date, rows]) => ({
    date,
    rows,
    starts: rows.filter((row) => row.kind === "candidate").length,
  }));
}


function formatImportDate(value: string | null): string {
  if (!value) return "Дата не указана";
  const [year, month, day] = value.split("-");
  return `${day}.${month}.${year}`;
}

function displayImportTime(row: ActiveScheduleImportRow): string {
  if (!row.time_from) return "Время не указано";
  const start = row.time_from.slice(0, 5);
  return row.time_to ? `${start}–${row.time_to.slice(0, 5)}` : start;
}

function ScheduleImportAssignments({ refreshKey }: { refreshKey: number }) {
  const { pushToast } = useToast();
  const [snapshot, setSnapshot] = useState<ActiveScheduleImportRows | null>(null);
  const [directory, setDirectory] = useState<UserListItem[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [bulkOwner, setBulkOwner] = useState("");
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [savingKey, setSavingKey] = useState<string | null>(null);
  const [exporting, setExporting] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const current = await listActiveScheduleImportRows();
      setSnapshot(current);
      setSelected([]);
    } catch {
      setSnapshot(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load, refreshKey]);

  useEffect(() => {
    let cancelled = false;
    void listHrUsers()
      .then((page) => {
        if (!cancelled) setDirectory(page.items);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, []);

  const people = useMemo(
    () => snapshot?.rows.filter((row) => row.row_type === "person") ?? [],
    [snapshot]
  );
  const unassignedPeopleCount = people.filter((row) => row.owner_user_id === null).length;

  const applyAssignment = async (rowKeys: string[], ownerValue: string) => {
    if (!ownerValue || saving || rowKeys.length === 0) return;
    if (ownerValue === "__unassigned__") {
      const message =
        rowKeys.length === 1
          ? `Снять назначение HR у «${people.find((row) => row.row_key === rowKeys[0])?.full_name ?? "кандидата"}»?`
          : `Снять назначение HR у ${rowKeys.length} выбранных строк?`;
      if (!window.confirm(`${message} Неназначенные кандидаты не попадают в очередь HR и аналитику.`)) {
        setBulkOwner("");
        setSavingKey(null);
        return;
      }
    }
    setSaving(true);
    try {
      const result = await assignScheduleImportRows({
        row_keys: rowKeys,
        owner_user_id: ownerValue === "__unassigned__" ? null : ownerValue,
      });
      pushToast(
        "success",
        result.updated === 0
          ? "Назначение не изменилось."
          : `Обновлено назначений: ${result.updated}. Изменение записано в аудит.`
      );
      setBulkOwner("");
      await load();
    } catch (caught) {
      pushToast(
        "danger",
        caught instanceof ApiError ? caught.message : "Не удалось назначить ответственного."
      );
    } finally {
      setSaving(false);
      setSavingKey(null);
    }
  };

  const assignOne = (row: ActiveScheduleImportRow, ownerValue: string) => {
    if (ownerValue === (row.owner_user_id ?? "__unassigned__")) return;
    setSavingKey(row.row_key);
    void applyAssignment([row.row_key], ownerValue);
  };

  const assignSelected = () => {
    void applyAssignment(selected, bulkOwner);
  };

  const exportCurrent = async () => {
    if (exporting) return;
    setExporting(true);
    try {
      const { blob, filename } = await exportCurrentScheduleImportXlsx();
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = filename;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      URL.revokeObjectURL(url);
      pushToast("success", `Экспортировано людей: ${snapshot?.active_people ?? 0}.`);
    } catch (caught) {
      pushToast(
        "danger",
        caught instanceof ApiError ? caught.message : "Не удалось выгрузить актуальную таблицу."
      );
    } finally {
      setExporting(false);
    }
  };

  if (loading || !snapshot?.import_id || !snapshot.can_assign) return null;

  return (
    <section className="schedule-import-assignments no-print" aria-labelledby="schedule-import-rows-title">
      <header className="schedule-import-assignments-head">
        <div>
          <h2 id="schedule-import-rows-title">Люди из последнего импорта</h2>
          <p>
            {snapshot.active_people} человек · {snapshot.file_name ?? "исходная таблица"}. Строки без даты и ответственного тоже сохранены.
          </p>
          {unassignedPeopleCount > 0 && (
            <p className="schedule-import-analytics-note" role="note">
              Назначьте HR: неназначенные кандидаты попадут в аналитику только после ручного назначения.
            </p>
          )}
        </div>
        <Button icon="download" onClick={() => void exportCurrent()} loading={exporting}>
          Экспортировать актуальную таблицу
        </Button>
      </header>

      {people.length > 0 && (
        <>
          <div className="schedule-import-bulk-actions">
            <label className="schedule-checkbox">
              <input
                type="checkbox"
                aria-label="Выбрать всех людей"
                checked={selected.length === people.length && people.length > 0}
                onChange={(event) =>
                  setSelected(event.target.checked ? people.map((row) => row.row_key) : [])
                }
              />
              Выбрать всех ({people.length})
            </label>
            <SelectInput
              aria-label="Ответственный HR для выбранных строк"
              value={bulkOwner}
              onChange={(event) => setBulkOwner(event.target.value)}
            >
              <option value="">Выберите HR для массового назначения</option>
              <option value="__unassigned__">Снять назначение</option>
              {directory.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.full_name || item.username}
                </option>
              ))}
            </SelectInput>
            <Button
              size="sm"
              onClick={assignSelected}
              loading={saving && savingKey === null}
              disabled={selected.length === 0 || !bulkOwner || saving}
            >
              Назначить выбранным ({selected.length})
            </Button>
          </div>

          <div className="schedule-import-table-wrap">
            <table className="schedule-table schedule-import-current-table">
              <thead>
                <tr>
                  <th scope="col">Выбор</th>
                  <th scope="col">Строка</th>
                  <th scope="col">ФИО</th>
                  <th scope="col">Дата / время</th>
                  <th scope="col">Должность</th>
                  <th scope="col">Состояние</th>
                  <th scope="col">Ответственный HR</th>
                </tr>
              </thead>
              <tbody>
                {people.map((row) => (
                  <tr key={row.row_key}>
                    <td>
                      <input
                        type="checkbox"
                        aria-label={`Выбрать строку ${row.sheet_row}`}
                        checked={selected.includes(row.row_key)}
                        onChange={(event) =>
                          setSelected((current) =>
                            event.target.checked
                              ? [...current, row.row_key]
                              : current.filter((key) => key !== row.row_key)
                          )
                        }
                      />
                    </td>
                    <td>{row.sheet_row}</td>
                    <td>{row.full_name || "Без ФИО"}</td>
                    <td>
                      {formatImportDate(row.entry_date)}
                      <br />
                      {displayImportTime(row)}
                    </td>
                    <td>{row.position || "—"}</td>
                    <td>{row.schedule_ready ? "Готов к графику" : "Не готов: нет даты"}</td>
                    <td>
                      {row.owner_name ? (
                        <span>{row.owner_name}</span>
                      ) : (
                        <span className="schedule-unassigned">Не назначен</span>
                      )}
                      <SelectInput
                        aria-label={`Ответственный HR для строки ${row.sheet_row}`}
                        value={row.owner_user_id ?? "__unassigned__"}
                        disabled={saving}
                        onChange={(event) => assignOne(row, event.target.value)}
                      >
                        <option value="__unassigned__">
                          {row.owner_user_id ? "Снять назначение" : "Не назначен"}
                        </option>
                        {directory.map((item) => (
                          <option key={item.id} value={item.id}>
                            {item.full_name || item.username}
                          </option>
                        ))}
                      </SelectInput>
                      {savingKey === row.row_key && <span className="schedule-import-saving">Сохраняем…</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </section>
  );
}

/**
 * «График выхода»: дневные блоки с выходами кандидатов и служебными строками,
 * серверные фильтры, быстрый перенос даты/времени прямо в строке, выгрузка в
 * Excel и печать (print-CSS показывает только таблицу, альбомная ориентация).
 */
export default function SchedulePage({ user, onOpenCandidate }: SchedulePageProps) {
  const { pushToast } = useToast();
  const canSeeAll = user.role !== "hr";

  const [period, setPeriod] = useState<Period>(() => presetRange("two_weeks"));
  const [organization, setOrganization] = useState("");
  const [department, setDepartment] = useState("");
  const [position, setPosition] = useState("");
  const [shift, setShift] = useState("");
  const [ownerId, setOwnerId] = useState("");
  const [stage, setStage] = useState<CandidateStage | "">("");
  const [search, setSearch] = useState("");
  const [includeRejected, setIncludeRejected] = useState(false);

  const [data, setData] = useState<WorkScheduleList | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [reloadTick, setReloadTick] = useState(0);
  const [exporting, setExporting] = useState(false);

  const [suggestions, setSuggestions] = useState<WorkScheduleSuggestions | null>(null);
  const [directory, setDirectory] = useState<UserListItem[]>([]);

  const [entryModal, setEntryModal] = useState<{ date: string } | null>(null);
  const [editing, setEditing] = useState<RowEdit | null>(null);
  const [savingRow, setSavingRow] = useState(false);
  const [importOpen, setImportOpen] = useState(false);

  const query = useMemo<WorkScheduleQuery>(
    () => ({
      from: period.from || undefined,
      to: period.to || undefined,
      organization: organization.trim() || undefined,
      department: department.trim() || undefined,
      position: position.trim() || undefined,
      shift: shift.trim() || undefined,
      owner: canSeeAll && ownerId ? ownerId : undefined,
      stage: (stage || undefined) as CandidateStage | undefined,
      q: search.trim() || undefined,
      include_rejected: includeRejected || undefined,
    }),
    [
      period.from,
      period.to,
      organization,
      department,
      position,
      shift,
      ownerId,
      canSeeAll,
      stage,
      search,
      includeRejected,
    ]
  );

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setData(await listWorkSchedule(query));
    } catch (caught) {
      setData(null);
      setError(
        caught instanceof ApiError ? caught.message : "Не удалось загрузить график выхода."
      );
    } finally {
      setLoading(false);
    }
  }, [query]);

  useEffect(() => {
    void load();
  }, [load, reloadTick]);

  useEffect(() => {
    let cancelled = false;
    void fetchWorkScheduleSuggestions()
      .then((value) => {
        if (!cancelled) setSuggestions(value);
      })
      .catch(() => {
        // Подсказки — удобство, не критичный путь: график работает и без них.
      });
    return () => {
      cancelled = true;
    };
  }, [reloadTick]);

  useEffect(() => {
    if (!canSeeAll) return;
    let cancelled = false;
    void listHrUsers()
      .then((page) => {
        if (!cancelled) setDirectory(page.items);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [canSeeAll]);

  const days = useMemo(() => groupByDay(data?.items ?? []), [data]);
  const canEditRow = (row: WorkScheduleRow) => rowIsEditable(row, user);
  // Эффективный флаг отказавшихся приходит с сервера: явный фильтр по этапу
  // «Отказ» включает их даже при выключенном переключателе.
  const rejectedShown = data?.include_rejected ?? includeRejected;

  const applyPreset = (preset: Exclude<PeriodPreset, "custom">) => setPeriod(presetRange(preset));

  const changePeriod = (patch: Partial<Pick<Period, "from" | "to">>) => {
    setPeriod((current) => ({ ...current, ...patch, preset: "custom" }));
  };

  const startEditing = (row: WorkScheduleRow) => {
    setEditing({
      id: row.id,
      kind: row.kind,
      date: row.entry_date,
      time: row.start_time ? row.start_time.slice(0, 5) : "",
    });
  };

  const saveRow = async () => {
    if (!editing || savingRow) return;
    setSavingRow(true);
    try {
      if (editing.kind === "candidate") {
        // Дату/время кандидата переносим через карточку — сервер пишет аудит
        // `candidate_start_schedule_changed` (было → стало).
        await updateCandidate(editing.id, {
          start_date: editing.date,
          start_time: editing.time || null,
        });
      } else {
        await updateScheduleEntry(editing.id, {
          entry_date: editing.date,
          time_from: editing.time || null,
        });
      }
      pushToast("success", "Дата и время обновлены — изменение записано в аудит.");
      setEditing(null);
      setReloadTick((tick) => tick + 1);
    } catch (caught) {
      pushToast(
        "danger",
        caught instanceof ApiError ? caught.message : "Не удалось сохранить изменения."
      );
    } finally {
      setSavingRow(false);
    }
  };

  const handleExport = async () => {
    if (exporting) return;
    setExporting(true);
    try {
      const { blob, filename } = await exportWorkScheduleXlsx(query);
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = filename;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      URL.revokeObjectURL(url);
      pushToast("success", "График выгружен в Excel.");
    } catch (caught) {
      pushToast(
        "danger",
        caught instanceof ApiError ? caught.message : "Не удалось выгрузить график."
      );
    } finally {
      setExporting(false);
    }
  };

  const hasFilters =
    Boolean(organization || department || position || shift || ownerId || stage || search) ||
    includeRejected;

  const resetFilters = () => {
    setOrganization("");
    setDepartment("");
    setPosition("");
    setShift("");
    setOwnerId("");
    setStage("");
    setSearch("");
    setIncludeRejected(false);
  };

  return (
    <div className="schedule-page">
      <div className="schedule-toolbar no-print">
        <div className="schedule-period">
          <div className="schedule-presets" role="group" aria-label="Период графика">
            <Button
              size="sm"
              variant={period.preset === "this_week" ? "primary" : "secondary"}
              onClick={() => applyPreset("this_week")}
            >
              Эта неделя
            </Button>
            <Button
              size="sm"
              variant={period.preset === "next_week" ? "primary" : "secondary"}
              onClick={() => applyPreset("next_week")}
            >
              Следующая
            </Button>
            <Button
              size="sm"
              variant={period.preset === "month" ? "primary" : "secondary"}
              onClick={() => applyPreset("month")}
            >
              Месяц
            </Button>
            <Button
              size="sm"
              variant={period.preset === "custom" ? "primary" : "secondary"}
              onClick={() => setPeriod((current) => ({ ...current, preset: "custom" }))}
            >
              Свой период
            </Button>
          </div>
          <div className="schedule-dates">
            <Field label="С">
              {(id) => (
                <TextInput
                  id={id}
                  type="date"
                  value={period.from}
                  onChange={(event) => changePeriod({ from: event.target.value })}
                />
              )}
            </Field>
            <Field label="По">
              {(id) => (
                <TextInput
                  id={id}
                  type="date"
                  value={period.to}
                  onChange={(event) => changePeriod({ to: event.target.value })}
                />
              )}
            </Field>
            <span className="schedule-period-label">
              {period.from && period.to ? formatPeriodLabel(period.from, period.to) : ""}
            </span>
          </div>
        </div>

        <div className="schedule-actions">
          <Button
            icon="download"
            onClick={() => void handleExport()}
            loading={exporting}
            disabled={loading || Boolean(error)}
          >
            Скачать Excel
          </Button>
          <Button icon="print" onClick={() => window.print()} disabled={loading || !data}>
            Печать
          </Button>
          <Button
            variant="secondary"
            icon="upload"
            onClick={() => setImportOpen(true)}
            title="Импорт графика выхода из Excel: сначала предпросмотр, затем подтверждение"
            disabled={loading}
          >
            Импорт графика из Excel
          </Button>
        </div>
      </div>

      <div className="schedule-filters no-print">
        <div className="schedule-search" role="search">
          <Icon name="search" size={15} />
          <TextInput
            type="search"
            value={search}
            aria-label="Поиск по графику"
            placeholder="Поиск: ФИО, организация, отдел, комментарий"
            onChange={(event) => setSearch(event.target.value)}
          />
        </div>
        <Field label="Организация">
          {(id) => (
            <TextInput
              id={id}
              list="schedule-filter-organizations"
              value={organization}
              onChange={(event) => setOrganization(event.target.value)}
            />
          )}
        </Field>
        <Field label="Отдел">
          {(id) => (
            <TextInput
              id={id}
              list="schedule-filter-departments"
              value={department}
              onChange={(event) => setDepartment(event.target.value)}
            />
          )}
        </Field>
        <Field label="Должность">
          {(id) => (
            <TextInput
              id={id}
              value={position}
              onChange={(event) => setPosition(event.target.value)}
            />
          )}
        </Field>
        <Field label="Смена">
          {(id) => (
            <TextInput
              id={id}
              list="schedule-filter-shifts"
              value={shift}
              onChange={(event) => setShift(event.target.value)}
            />
          )}
        </Field>
        {canSeeAll && (
          <Field label="Ответственный HR">
            {(id) => (
              <SelectInput
                id={id}
                value={ownerId}
                onChange={(event) => setOwnerId(event.target.value)}
              >
                <option value="">Все</option>
                {directory.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.full_name || item.username}
                  </option>
                ))}
              </SelectInput>
            )}
          </Field>
        )}
        <Field label="Этап">
          {(id) => (
            <SelectInput
              id={id}
              value={stage}
              onChange={(event) => setStage(event.target.value as CandidateStage | "")}
            >
              <option value="">Все этапы</option>
              {CANDIDATE_STAGE_ORDER.map((item) => (
                <option key={item} value={item}>
                  {STAGE_LABELS[item]}
                </option>
              ))}
            </SelectInput>
          )}
        </Field>
        <label className="schedule-checkbox">
          <input
            type="checkbox"
            checked={includeRejected}
            onChange={(event) => setIncludeRejected(event.target.checked)}
          />
          Показать отказавшихся
        </label>
        {hasFilters && (
          <Button size="sm" variant="ghost" icon="close" onClick={resetFilters}>
            Сбросить фильтры
          </Button>
        )}
        <datalist id="schedule-filter-organizations">
          {(suggestions?.organizations ?? []).map((value) => (
            <option key={value} value={value} />
          ))}
        </datalist>
        <datalist id="schedule-filter-departments">
          {(suggestions?.departments ?? []).map((value) => (
            <option key={value} value={value} />
          ))}
        </datalist>
        <datalist id="schedule-filter-shifts">
          {(suggestions?.shifts ?? []).map((value) => (
            <option key={value} value={value} />
          ))}
        </datalist>
      </div>

      {loading && <SkeletonRows rows={6} columns={4} />}
      {!loading && error && <ErrorState onRetry={() => setReloadTick((tick) => tick + 1)} />}
      {!loading && !error && days.length === 0 && (
        <EmptyState
          title="На выбранный период выходов нет"
          description="Укажите дату выхода в карточке кандидата или добавьте служебную запись в дне графика."
        />
      )}

      {!loading && !error && days.length > 0 && (
        <div className="schedule-print" id="schedule-print-area">
          <header className="schedule-print-head">
            <h2>График выхода на работу</h2>
            <p className="schedule-print-sub">
              Период: {formatPeriodLabel(period.from, period.to)} · выходов:{" "}
              {data?.items.filter((row) => row.kind === "candidate").length ?? 0}
              {rejectedShown ? " · включая отказавшихся" : ""}
            </p>
          </header>

          {days.map((day) => (
            <section key={day.date} className="schedule-day" aria-label={formatDayHeading(day.date)}>
              <header className="schedule-day-head">
                <h3 className="schedule-day-title">{formatDayHeading(day.date)}</h3>
                <span className="schedule-day-count">выходов: {day.starts}</span>
                <span className="schedule-day-add no-print">
                  <Button
                    size="sm"
                    variant="secondary"
                    icon="plus"
                    onClick={() => setEntryModal({ date: day.date })}
                  >
                    Служебная запись
                  </Button>
                </span>
              </header>

              <table className="schedule-table">
                <thead>
                  <tr>
                    <th scope="col">№</th>
                    <th scope="col">ФИО</th>
                    <th scope="col">Время</th>
                    <th scope="col">Организация</th>
                    <th scope="col">Отдел</th>
                    <th scope="col">Должность</th>
                    <th scope="col">Смена</th>
                    <th scope="col">Комментарий</th>
                    <th scope="col">Ответственный HR</th>
                    <th scope="col" className="no-print">
                      <span className="sr-only">Действия</span>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {day.rows.map((row) =>
                    editing?.id === row.id ? (
                      <tr key={`${row.kind}-${row.id}`} className="schedule-row is-editing">
                        <td>{row.number}</td>
                        <td>
                          <input
                            type="date"
                            aria-label={`Дата выхода: ${row.display_name}`}
                            value={editing.date}
                            onChange={(event) =>
                              setEditing({ ...editing, date: event.target.value })
                            }
                          />
                        </td>
                        <td>
                          <input
                            type="time"
                            aria-label={`Время выхода: ${row.display_name}`}
                            value={editing.time}
                            onChange={(event) =>
                              setEditing({ ...editing, time: event.target.value })
                            }
                          />
                        </td>
                        <td colSpan={5}>
                          <div className="schedule-edit-actions">
                            <Button size="sm" loading={savingRow} onClick={() => void saveRow()}>
                              Сохранить
                            </Button>
                            <Button
                              size="sm"
                              variant="secondary"
                              onClick={() => setEditing(null)}
                              disabled={savingRow}
                            >
                              Отмена
                            </Button>
                          </div>
                        </td>
                        <td>{row.owner_username ?? "Не назначен"}</td>
                        <td />
                      </tr>
                    ) : (
                      <tr
                        key={`${row.kind}-${row.id}`}
                        className={row.kind === "entry" ? "schedule-row is-service" : "schedule-row"}
                      >
                        <td className="schedule-number">{row.number}</td>
                        <td className="schedule-name">
                          {row.kind === "candidate" && row.candidate_id ? (
                            <button
                              type="button"
                              className="schedule-name-link"
                              onClick={() => onOpenCandidate(row.candidate_id as string)}
                            >
                              {row.full_name ?? row.display_name}
                            </button>
                          ) : (
                            <span>{row.display_name}</span>
                          )}
                          {row.status !== "planned" && (
                            <span className="schedule-status">
                              <Badge tone={row.status === "started" ? "success" : "amber"}>
                                {row.status_label}
                              </Badge>
                            </span>
                          )}
                        </td>
                        <td className="schedule-time">{displayTime(row.start_time, row.end_time)}</td>
                        <td>{row.organization ?? "—"}</td>
                        <td>{row.department ?? "—"}</td>
                        <td>{row.position || "—"}</td>
                        <td>{row.shift ?? "—"}</td>
                        <td>{row.comment ?? "—"}</td>
                        <td>{row.owner_username ?? "Не назначен"}</td>
                        <td className="no-print">
                          {canEditRow(row) ? (
                            <IconButton
                              icon="edit"
                              size="sm"
                              label={`Изменить дату/время: ${row.display_name}`}
                              onClick={() => startEditing(row)}
                            />
                          ) : (
                            // Чужую служебную строку сервер править не даёт
                            // (403): не показываем кнопку, которая не сработает.
                            <span
                              className="schedule-readonly-flag"
                              title="Служебную строку ведёт её автор или ответственный за всех кандидатов"
                            >
                              только чтение
                            </span>
                          )}
                          {row.kind === "entry" && (
                            <span className="schedule-service-flag" title="Служебная строка">
                              служебная
                            </span>
                          )}
                        </td>
                      </tr>
                    )
                  )}
                </tbody>
              </table>
            </section>
          ))}
        </div>
      )}

      <ScheduleImportAssignments refreshKey={reloadTick} />

      {entryModal && (
        <ScheduleEntryModal
          open
          entryDate={entryModal.date}
          suggestions={suggestions}
          onClose={() => setEntryModal(null)}
          onSaved={() => setReloadTick((tick) => tick + 1)}
        />
      )}

      {importOpen && (
        <ScheduleImportDialog
          onClose={() => setImportOpen(false)}
          onImported={() => setReloadTick((tick) => tick + 1)}
        />
      )}
    </div>
  );
}
