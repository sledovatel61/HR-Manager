import { type ReactNode, useEffect, useRef, useState } from "react";
import { Sidebar } from "./Sidebar";
import { Topbar } from "./Topbar";
import { CommandPalette } from "../command/CommandPalette";
import "./shell.css";

export function AppShell({ children }: { children: ReactNode }) {
  const [paletteOpen, setPaletteOpen] = useState(false);
  const mainRef = useRef<HTMLElement>(null);

  useEffect(() => {
    function handleGlobalKey(e: KeyboardEvent) {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setPaletteOpen((v) => !v);
      }
    }
    document.addEventListener("keydown", handleGlobalKey);
    return () => document.removeEventListener("keydown", handleGlobalKey);
  }, []);

  return (
    <div className="app-shell">
      {/*
        Skip-link фокусирует контент программно (а не через href="#..."),
        чтобы не конфликтовать с hash-роутером прототипа (см. src/router.tsx),
        для которого "#main-content" выглядел бы как неизвестный маршрут.
      */}
      <a
        href="#main-content"
        className="skip-link"
        onClick={(event) => {
          event.preventDefault();
          mainRef.current?.focus();
        }}
      >
        Перейти к основному содержимому
      </a>
      <Sidebar />
      <div className="app-shell-main">
        <Topbar onOpenPalette={() => setPaletteOpen(true)} />
        <main id="main-content" ref={mainRef} className="app-shell-content" tabIndex={-1}>
          {children}
        </main>
      </div>
      <CommandPalette open={paletteOpen} onClose={() => setPaletteOpen(false)} />
    </div>
  );
}
