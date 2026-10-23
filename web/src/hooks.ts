import { useCallback, useEffect, useRef, useState } from "react";

export function useLoad<T>(load: () => Promise<T>, deps: unknown[], pollMs?: number) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const loadRef = useRef(load);
  loadRef.current = load;

  const reload = useCallback(async () => {
    try {
      const value = await loadRef.current();
      setData(value);
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    setLoading(true);
    reload();
    if (!pollMs) return;
    const id = window.setInterval(() => {
      if (document.visibilityState === "visible") reload();
    }, pollMs);
    return () => window.clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  return { data, error, loading, reload, setData };
}

export function useTitle(title: string) {
  useEffect(() => {
    document.title = title ? `${title} | Watchful` : "Watchful";
  }, [title]);
}

// Arrow keys move between tabs, as screen reader users expect from a tab list.
export function tabKeys<T extends string>(ids: readonly T[], current: T, select: (id: T) => void) {
  return (e: React.KeyboardEvent<HTMLElement>) => {
    const i = ids.indexOf(current);
    const next = e.key === "ArrowRight" ? ids[(i + 1) % ids.length] : e.key === "ArrowLeft" ? ids[(i - 1 + ids.length) % ids.length] : null;
    if (!next) return;
    e.preventDefault();
    select(next);
    const tabs = e.currentTarget.querySelectorAll<HTMLElement>('[role="tab"]');
    tabs[ids.indexOf(next)]?.focus();
  };
}
