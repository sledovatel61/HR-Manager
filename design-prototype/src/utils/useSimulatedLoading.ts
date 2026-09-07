import { useEffect, useState } from "react";

/**
 * Короткая имитация загрузки при первом монтировании страницы — нужна,
 * чтобы продемонстрировать skeleton-состояния прототипа (обязательный
 * экран "Loading state"). Не является таймером реального запроса.
 */
export function useSimulatedLoading(durationMs = 550): boolean {
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    const timer = window.setTimeout(() => setLoading(false), durationMs);
    return () => window.clearTimeout(timer);
  }, [durationMs]);
  return loading;
}
