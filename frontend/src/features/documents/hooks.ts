import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "../../api";
export function errorText(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 403)
      return "Недостаточно прав. Обратитесь к администратору.";
    if (error.status === 409)
      return `Конфликт версии: ${error.message} Обновите данные и повторите.`;
    return error.message;
  }
  return "Не удалось выполнить запрос. Повторите попытку.";
}
export function useResource<T>(loader: () => Promise<T>) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const generation = useRef(0);
  const reload = useCallback(async () => {
    const id = ++generation.current;
    setLoading(true);
    setError("");
    try {
      const result = await loader();
      if (id === generation.current) setData(result);
    } catch (e) {
      if (id === generation.current) setError(errorText(e));
    } finally {
      if (id === generation.current) setLoading(false);
    }
  }, [loader]);
  useEffect(() => {
    void reload();
    const invalidate = () => {
      generation.current++;
    };
    return invalidate;
  }, [reload]);
  return { data, setData, error, loading, reload };
}
