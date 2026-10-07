import { describe, expect, it } from "vitest";
import board from "./kanban.css?raw";
import drawer from "./drawer.css?raw";
import schedule from "../schedule/schedule.css?raw";
import bell from "../notifications/notifications.css?raw";
import legacy from "../../styles.css?raw";
import surfaces from "../../design-system/components/bentoSurface.css?raw";

// Structural guards complement the real-browser overflow/geometry acceptance run.
function rule(css: string, selector: string) {
  const start = css.indexOf(`${selector} {`);
  expect(start, `missing CSS selector ${selector}`).toBeGreaterThanOrEqual(0);
  return css.slice(start, css.indexOf("}", start) + 1);
}

describe("layout and semantic theme contracts", () => {
  it("keeps the mockup's 296px columns and 16px gap; the board owns scrolling", () => {
    expect(rule(board, ".kanban-board")).toContain("overflow: auto");
    expect(rule(board, ".kanban-board")).toContain("gap: var(--space-4)");
    expect(rule(board, ".kanban-board")).toContain("max-height:");
    expect(rule(board, ".kanban-column")).toContain("flex: 0 0 296px");
    expect(rule(board, ".kanban-column")).toContain("width: 296px");
    expect(rule(board, ".kanban-column")).toContain("min-width: 296px");
    expect(rule(board, ".kanban-column")).not.toContain("max-height");
    expect(rule(board, ".kanban-cards")).not.toContain("overflow");
  });

  it("does not turn schedule table cells into flex boxes or reuse legacy detail rows", () => {
    expect(rule(schedule, ".schedule-name")).not.toContain("display: flex");
    expect(rule(schedule, ".schedule-name")).toContain("vertical-align: top");
    expect(drawer).toContain(".candidate-detail-row");
    expect(drawer).not.toMatch(/(?:^|\n)\.detail-row/);
    expect(drawer).toContain(".drawer-content > .tabs");
  });

  it("resets full-width notification buttons and uses semantic surfaces", () => {
    const row = rule(bell, ".bell-item");
    for (const declaration of ["width: 100%", "border: 0", "font: inherit", "background: transparent", "color: var(--text-primary)"]) {
      expect(row).toContain(declaration);
    }
    expect(rule(bell, ".bell-popover")).toContain("background: var(--surface-canvas)");
    expect(bell).toContain(".bell-item:focus-visible");
    expect(bell).not.toMatch(/#[0-9a-f]{3,8}\b|rgba?\(/i);
  });

  it("keeps native controls and legacy surfaces theme-aware, including dark glass", () => {
    expect(legacy).toContain('color-scheme: dark');
    expect(legacy).toContain("option");
    expect(rule(legacy, ".panel")).toContain("background: var(--surface-raised)");
    expect(legacy).not.toMatch(/#[0-9a-f]{3,8}\b|rgba?\(/i);
    const dark = surfaces.slice(surfaces.indexOf('[data-theme="dark"]'));
    for (const token of ["--drawer-bg", "--topbar-bg", "--table-head-bg", "--card-shadow"]) {
      expect(dark).toContain(token);
    }
  });
});
