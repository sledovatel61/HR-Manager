import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AppearanceProvider, THEME_STORAGE_KEY, useAppearance } from "./appearance";

function Harness() {
  const { theme, density, setTheme, setDensity, toggleTheme } = useAppearance();
  return (
    <div>
      <span data-testid="theme">{theme}</span>
      <span data-testid="density">{density}</span>
      <button type="button" onClick={() => setTheme("dark")}>
        setDark
      </button>
      <button type="button" onClick={() => setTheme("light")}>
        setLight
      </button>
      <button type="button" onClick={() => setDensity("compact")}>
        setCompact
      </button>
      <button type="button" onClick={toggleTheme}>
        toggle
      </button>
    </div>
  );
}

function mockMatchMedia(dark: boolean) {
  window.matchMedia = vi.fn().mockImplementation((query: string) => ({
    matches: dark && query.includes("dark"),
    media: query,
    onchange: null,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    addListener: vi.fn(),
    removeListener: vi.fn(),
    dispatchEvent: vi.fn(),
  })) as unknown as typeof window.matchMedia;
}

beforeEach(() => {
  localStorage.clear();
  document.documentElement.removeAttribute("data-theme");
  document.documentElement.removeAttribute("data-density");
  mockMatchMedia(false);
});

afterEach(() => cleanup());

describe("AppearanceProvider", () => {
  it("по умолчанию — светлая тема и комфортная плотность, применяет data-атрибуты и сохраняет", () => {
    render(
      <AppearanceProvider>
        <Harness />
      </AppearanceProvider>,
    );
    expect(screen.getByTestId("theme")).toHaveTextContent("light");
    expect(screen.getByTestId("density")).toHaveTextContent("comfortable");
    expect(document.documentElement).toHaveAttribute("data-theme", "light");
    expect(document.documentElement).toHaveAttribute("data-density", "comfortable");
    expect(JSON.parse(localStorage.getItem(THEME_STORAGE_KEY)!)).toEqual({
      theme: "light",
      density: "comfortable",
    });
  });

  it("следует prefers-color-scheme: dark, когда выбор не сохранён", () => {
    mockMatchMedia(true);
    render(
      <AppearanceProvider>
        <Harness />
      </AppearanceProvider>,
    );
    expect(screen.getByTestId("theme")).toHaveTextContent("dark");
    expect(document.documentElement).toHaveAttribute("data-theme", "dark");
  });

  it("переключает тему через toggleTheme и сохраняет выбор", () => {
    render(
      <AppearanceProvider>
        <Harness />
      </AppearanceProvider>,
    );
    fireEvent.click(screen.getByText("toggle"));
    expect(screen.getByTestId("theme")).toHaveTextContent("dark");
    expect(document.documentElement).toHaveAttribute("data-theme", "dark");
    expect(JSON.parse(localStorage.getItem(THEME_STORAGE_KEY)!).theme).toBe("dark");

    fireEvent.click(screen.getByText("toggle"));
    expect(screen.getByTestId("theme")).toHaveTextContent("light");
    expect(document.documentElement).toHaveAttribute("data-theme", "light");
  });

  it("меняет плотность через setDensity и применяет data-density", () => {
    render(
      <AppearanceProvider>
        <Harness />
      </AppearanceProvider>,
    );
    fireEvent.click(screen.getByText("setCompact"));
    expect(screen.getByTestId("density")).toHaveTextContent("compact");
    expect(document.documentElement).toHaveAttribute("data-density", "compact");
    expect(JSON.parse(localStorage.getItem(THEME_STORAGE_KEY)!).density).toBe("compact");
  });

  it("при битом storage откатывается к умолчанию/системе и перезаписывает валидным значением", () => {
    localStorage.setItem(THEME_STORAGE_KEY, "{ это не JSON");
    render(
      <AppearanceProvider>
        <Harness />
      </AppearanceProvider>,
    );
    expect(screen.getByTestId("theme")).toHaveTextContent("light");
    expect(JSON.parse(localStorage.getItem(THEME_STORAGE_KEY)!)).toEqual({
      theme: "light",
      density: "comfortable",
    });
  });

  it("сохраняет валидные ключи и игнорирует невалидные (например, несуществующая тема)", () => {
    localStorage.setItem(
      THEME_STORAGE_KEY,
      JSON.stringify({ theme: "neon", density: "compact" }),
    );
    render(
      <AppearanceProvider>
        <Harness />
      </AppearanceProvider>,
    );
    expect(screen.getByTestId("theme")).toHaveTextContent("light"); // невалидная тема → дефолт
    expect(screen.getByTestId("density")).toHaveTextContent("compact"); // валидная плотность сохранена
  });

  it("восстанавливает сохранённый выбор после размонтирования/повторного монтирования (перезагрузка)", () => {
    const first = render(
      <AppearanceProvider>
        <Harness />
      </AppearanceProvider>,
    );
    fireEvent.click(screen.getByText("setDark"));
    fireEvent.click(screen.getByText("setCompact"));
    expect(localStorage.getItem(THEME_STORAGE_KEY)).toContain("dark");
    first.unmount();

    render(
      <AppearanceProvider>
        <Harness />
      </AppearanceProvider>,
    );
    expect(screen.getByTestId("theme")).toHaveTextContent("dark");
    expect(screen.getByTestId("density")).toHaveTextContent("compact");
    expect(document.documentElement).toHaveAttribute("data-theme", "dark");
    expect(document.documentElement).toHaveAttribute("data-density", "compact");
  });

  it("не следует за системной темой, если пользователь уже сделал явный выбор", () => {
    localStorage.setItem(
      THEME_STORAGE_KEY,
      JSON.stringify({ theme: "dark", density: "comfortable" }),
    );
    const addEventListener = vi.fn();
    const mql = {
      matches: false,
      media: "(prefers-color-scheme: dark)",
      onchange: null,
      addEventListener,
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
    };
    window.matchMedia = vi.fn().mockReturnValue(mql) as unknown as typeof window.matchMedia;

    render(
      <AppearanceProvider>
        <Harness />
      </AppearanceProvider>,
    );
    expect(screen.getByTestId("theme")).toHaveTextContent("dark");

    // Система переключилась на светлую — выбор пользователя не должен измениться.
    act(() => {
      const cb = addEventListener.mock.calls[0][1];
      cb({ matches: false });
    });
    expect(screen.getByTestId("theme")).toHaveTextContent("dark");
  });
});
