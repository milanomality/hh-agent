import { type DependencyList, useEffect, useState } from "react";
import { ApiError } from "./api";

interface PollState<T> {
  data: T | null;
  error: string | null;
  loading: boolean;
}

// Периодически дёргает fn (по умолчанию раз в 30с) и при смене deps. 401 → onAuthError.
export function usePolling<T>(
  fn: () => Promise<T>,
  deps: DependencyList,
  onAuthError: () => void,
  intervalMs = 30000,
): PollState<T> & { reload: () => void } {
  const [state, setState] = useState<PollState<T>>({ data: null, error: null, loading: true });
  const [tick, setTick] = useState(0);

  useEffect(() => {
    let alive = true;
    const run = async () => {
      try {
        const data = await fn();
        if (alive) setState({ data, error: null, loading: false });
      } catch (e) {
        if (e instanceof ApiError && e.status === 401) {
          onAuthError();
          return;
        }
        if (alive) {
          setState((s) => ({ ...s, error: e instanceof Error ? e.message : String(e), loading: false }));
        }
      }
    };
    void run();
    const t = setInterval(() => void run(), intervalMs);
    return () => {
      alive = false;
      clearInterval(t);
    };
    // fn намеренно вне зависимостей — рефетч контролируется deps/tick
  }, [...deps, tick, intervalMs]); // eslint-disable-line react-hooks/exhaustive-deps

  return { ...state, reload: () => setTick((n) => n + 1) };
}
