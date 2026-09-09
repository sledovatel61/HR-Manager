export function LoadState({
  loading,
  error,
  reload,
}: {
  loading: boolean;
  error: string;
  reload: () => Promise<void>;
}) {
  return (
    <>
      {loading && <p role="status">Загрузка…</p>}
      {error && (
        <div role="alert">
          <p>{error}</p>
          <button onClick={() => void reload()}>Повторить загрузку</button>
        </div>
      )}
    </>
  );
}
