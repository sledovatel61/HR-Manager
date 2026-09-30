/** Раздел «Настройки» (UX-замечания 2026-09-29, блок G): рабочий контур
 * отделён от редко используемых разделов; группы и подразделы доступны по
 * роли, прямые ссылки прежние. Серверные проверки прав не заменяются
 * скрытием пунктов — здесь проверяем только навигацию. */

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { SettingsPage } from "./SettingsPage";
import { settingsSectionsForRole } from "./settingsNav";

describe("SettingsPage", () => {
  it("показывает общие группы и ведёт в подразделы по прежним ссылкам", async () => {
    const onNavigate = vi.fn();
    render(<SettingsPage userRole="hr" onNavigate={onNavigate} />);

    expect(screen.getByRole("button", { name: /Настройки уведомлений/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Мои правила/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Списки документов/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Шаблоны документов/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Интеграции/ })).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: /Мои правила/ }));
    expect(onNavigate).toHaveBeenCalledWith("rules");
  });

  it("не показывает административные группы обычному HR", () => {
    render(<SettingsPage userRole="hr" onNavigate={() => undefined} />);
    expect(screen.queryAllByText("Администрирование")).toHaveLength(0);
    expect(screen.queryAllByText("Лицензия")).toHaveLength(0);
    expect(screen.queryAllByText("Пользователи")).toHaveLength(0);
    expect(screen.queryAllByText("Обновления")).toHaveLength(0);
  });

  it("руководитель видит «Обновления» и «Администрирование», но без лицензии и пользователей", () => {
    render(<SettingsPage userRole="manager" onNavigate={() => undefined} />);
    expect(screen.getAllByText("Обновления").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Администрирование").length).toBeGreaterThan(0);
    expect(screen.queryAllByText("Лицензия")).toHaveLength(0);
    expect(screen.queryAllByText("Пользователи")).toHaveLength(0);
  });

  it("администратор видит все группы", () => {
    render(<SettingsPage userRole="admin" onNavigate={() => undefined} />);
    for (const title of [
      "Уведомления",
      "Мои правила",
      "Контент и документы",
      "Интеграции",
      "Обновления",
      "Лицензия",
      "Администрирование",
      "Пользователи",
    ]) {
      expect(screen.getAllByText(title).length).toBeGreaterThan(0);
    }
  });

  it("settingsSectionsForRole отдаёт доступные подразделы для прямых ссылок", () => {
    expect(settingsSectionsForRole("hr")).toEqual(
      expect.arrayContaining(["preferences", "rules", "documents", "templates", "integrations"])
    );
    expect(settingsSectionsForRole("hr")).not.toEqual(expect.arrayContaining(["admin"]));
    expect(settingsSectionsForRole("admin")).toEqual(
      expect.arrayContaining(["license", "users", "admin", "updates"])
    );
    expect(settingsSectionsForRole("manager")).toEqual(
      expect.arrayContaining(["updates", "admin"])
    );
  });
});
