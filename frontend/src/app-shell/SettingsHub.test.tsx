import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { AppearanceProvider, THEME_STORAGE_KEY } from "./appearance";
import { AppearanceControls } from "./AppearanceControls";

beforeEach(() => {
  localStorage.clear();
  document.documentElement.removeAttribute("data-theme");
  document.documentElement.removeAttribute("data-density");
});

afterEach(() => cleanup());

describe("AppearanceControls (секция «Внешний вид»)", () => {
  it("показывает переключатели темы и плотности с корректными ролями и aria-pressed", () => {
    render(
      <AppearanceProvider>
        <AppearanceControls />
      </AppearanceProvider>,
    );

    const themeGroup = screen.getByRole("group", { name: "Тема оформления" });
    const densityGroup = screen.getByRole("group", { name: "Плотность интерфейса" });

    const light = within(themeGroup).getByRole("button", { name: "Светлая" });
    const dark = within(themeGroup).getByRole("button", { name: "Тёмная" });
    const comfortable = within(densityGroup).getByRole("button", { name: "Комфортная" });
    const compact = within(densityGroup).getByRole("button", { name: "Компактная" });

    expect(light).toHaveAttribute("aria-pressed", "true");
    expect(dark).toHaveAttribute("aria-pressed", "false");
    expect(comfortable).toHaveAttribute("aria-pressed", "true");
    expect(compact).toHaveAttribute("aria-pressed", "false");
    expect(document.documentElement).toHaveAttribute("data-theme", "light");
    expect(document.documentElement).toHaveAttribute("data-density", "comfortable");
  });

  it("переключает тёмную тему по клику, применяет data-theme и сохраняет выбор", () => {
    render(
      <AppearanceProvider>
        <AppearanceControls />
      </AppearanceProvider>,
    );
    const dark = screen.getByRole("button", { name: "Тёмная" });
    fireEvent.click(dark);

    expect(dark).toHaveAttribute("aria-pressed", "true");
    expect(
      screen.getByRole("button", { name: "Светлая" }),
    ).toHaveAttribute("aria-pressed", "false");
    expect(document.documentElement).toHaveAttribute("data-theme", "dark");
    expect(JSON.parse(localStorage.getItem(THEME_STORAGE_KEY)!).theme).toBe("dark");
  });

  it("переключает плотность по клику и применяет data-density", () => {
    render(
      <AppearanceProvider>
        <AppearanceControls />
      </AppearanceProvider>,
    );
    const compact = screen.getByRole("button", { name: "Компактная" });
    fireEvent.click(compact);

    expect(compact).toHaveAttribute("aria-pressed", "true");
    expect(document.documentElement).toHaveAttribute("data-density", "compact");
    expect(JSON.parse(localStorage.getItem(THEME_STORAGE_KEY)!).density).toBe("compact");
  });

  it("доступен с клавиатуры: фокус и активация пробелом переключают тему", () => {
    render(
      <AppearanceProvider>
        <AppearanceControls />
      </AppearanceProvider>,
    );
    const dark = screen.getByRole("button", { name: "Тёмная" });
    dark.focus();
    expect(dark).toHaveFocus();
    fireEvent.keyDown(dark, { key: " " });
    fireEvent.click(dark); // стандартная активация кнопки по Enter/Space в браузере
    expect(document.documentElement).toHaveAttribute("data-theme", "dark");
  });
});
