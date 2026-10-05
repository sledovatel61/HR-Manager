import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { QueueBarChart, QueueChart } from "./QueueCharts";
import { formatDays, formatInt, formatPercent, niceScale } from "./chartFormat";

const LABELS = ["29.09", "30.09", "01.10", "02.10", "03.10", "04.10", "05.10"];

describe("QueueChart", () => {
  it("рисует ряд, подписи оси и сводку для скринридера", () => {
    render(
      <QueueChart
        title="Новые кандидаты"
        description="Количество созданных кандидатов по дням."
        labels={LABELS}
        emptyText="За период данных нет."
        series={[
          {
            key: "created",
            label: "Создано",
            kind: "area",
            format: formatInt,
            values: [1, 0, 3, 5, 2, 0, 4],
          },
        ]}
      />,
    );

    const svg = screen.getByRole("img");
    // Сводка в названии: минимум/максимум/последнее видны без зрения.
    expect(svg.getAttribute("aria-label")).toContain("Новые кандидаты");
    expect(svg.getAttribute("aria-label")).toContain("последнее 4");
    // Кривая и семь точек ряда отрисованы.
    expect(svg.querySelectorAll("path.queue-chart-line")).toHaveLength(1);
    expect(svg.querySelectorAll(".queue-chart-dot")).toHaveLength(7);
    // Подписи оси — не только цвет.
    expect(svg.textContent).toContain("29.09");
    // Подпись ряда повторяется в легенде и в шапке таблицы.
    expect(screen.getAllByText("Создано").length).toBeGreaterThan(0);
  });

  it("различает ряды формой и типом линии, а не только цветом", () => {
    render(
      <QueueChart
        title="Динамика найма"
        description="Выходы и срок найма."
        labels={LABELS}
        emptyText="За период данных нет."
        series={[
          { key: "exits", label: "Выходы", kind: "area", values: [1, 2, 3, 4, 5, 6, 7] },
          {
            key: "days",
            label: "Срок найма",
            kind: "line",
            axis: "right",
            values: [10, 11, 12, 13, 14, 15, 16],
          },
        ]}
      />,
    );

    const svg = screen.getByRole("img");
    // Второй ряд — пунктир (кодирование не на одном цвете).
    expect(svg.querySelectorAll("path.queue-chart-line-dashed")).toHaveLength(1);
    // И маркер другой формы: прямоугольник вместо круга.
    expect(svg.querySelectorAll("rect.queue-chart-dot").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Срок найма").length).toBeGreaterThan(0);
  });

  it("даёт таблицу значений — значения доступны без наведения", () => {
    render(
      <QueueChart
        title="Средний срок найма"
        description="Дней от создания до найма."
        labels={LABELS}
        emptyText="За период данных нет."
        series={[{ key: "days", label: "Срок найма", kind: "line", format: formatDays, values: [10, null, 12, 13, null, 15, 16] }]}
      />,
    );

    const table = screen.getByRole("table");
    expect(within(table).getAllByRole("row")).toHaveLength(LABELS.length + 1);
    // Отсутствующее значение — «—», а не ноль.
    expect(within(table).getAllByText("—")).toHaveLength(2);
    expect(within(table).getByText("10 дн.")).toBeInTheDocument();
    expect(within(table).getByText("16 дн.")).toBeInTheDocument();
    // Заголовки колонок связаны с данными.
    expect(within(table).getAllByRole("columnheader").length).toBeGreaterThan(1);
  });

  it("на пустом периоде объясняет пустоту, а не рисует нули", () => {
    render(
      <QueueChart
        title="Новые кандидаты"
        description="Количество созданных кандидатов."
        labels={LABELS}
        emptyText="За период новых кандидатов не было."
        series={[{ key: "created", label: "Создано", kind: "area", values: [0, 0, 0] }]}
      />,
    );

    expect(screen.getByText("За период новых кандидатов не было.")).toBeInTheDocument();
    expect(screen.queryByRole("img")).toBeNull();
  });

  it("не показывает значений при отсутствии данных о Cohort-конверсии", () => {
    render(
      <QueueChart
        title="Конверсия"
        description="Доля дошедших."
        labels={LABELS}
        emptyText="Когорта пуста."
        series={[{ key: "rate", label: "Конверсия", kind: "line", format: formatPercent, values: [null, null, null] }]}
      />,
    );

    expect(screen.getByText("Когорта пуста.")).toBeInTheDocument();
    expect(screen.queryByRole("img")).toBeNull();
  });
});

describe("QueueBarChart", () => {
  it("подписывает значения рядом с полосой и даёт сводку", () => {
    render(
      <QueueBarChart
        title="Источники"
        description="Новые кандидаты по источникам."
        items={[
          { key: "site", label: "Сайт компании", value: 12 },
          { key: "referral", label: "Рекомендация", value: 3 },
        ]}
        emptyText="За период откликов нет."
      />,
    );

    const list = screen.getByRole("img");
    expect(list.getAttribute("aria-label")).toContain("Всего: 15");
    expect(list.getAttribute("aria-label")).toContain("Сайт компании: 12");
    // Значение видно без наведения (подпись рядом с полосой).
    const block = screen.getByRole("img").closest(".queue-chart") as HTMLElement;
    expect(within(block).getAllByText("12").length).toBeGreaterThan(0);
    expect(within(block).getAllByText("3").length).toBeGreaterThan(0);
    // И есть таблица с долями.
    const table = screen.getByRole("table");
    // Неразрывный пробел перед % — типографическое правило, а не опечатка.
    expect(within(table).getByText(/^80\s%$/)).toBeInTheDocument();
    expect(within(table).getByText(/^20\s%$/)).toBeInTheDocument();
  });

  it("объясняет отсутствие источников", () => {
    render(
      <QueueBarChart title="Источники" description="Пусто." items={[]} emptyText="За период откликов нет." />,
    );

    expect(screen.getByText("За период откликов нет.")).toBeInTheDocument();
    expect(screen.queryByRole("table")).toBeNull();
  });
});

describe("форматирование и шкала", () => {
  it("отсутствующее значение — «—», а не ноль", () => {
    expect(formatInt(null)).toBe("—");
    expect(formatPercent(null)).toBe("—");
    expect(formatDays(null)).toBe("—");
    expect(formatInt(0)).toBe("0");
    expect(formatPercent(41.25)).toBe("41,3\u00a0%");
    expect(formatDays(18.4)).toBe("18,4 дн.");
  });

  it("шкала начинается с нуля и округляется до понятных подписей", () => {
    const scale = niceScale(0, 7);
    expect(scale.min).toBe(0);
    expect(scale.max).toBeGreaterThanOrEqual(7);
    expect(scale.max % scale.step).toBe(0);
    // Пустые данные не приводят к делению на ноль.
    const empty = niceScale(0, 0);
    expect(empty.max).toBeGreaterThan(empty.min);
  });
});
