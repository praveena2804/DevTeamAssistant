import { useCallback, useEffect, useState } from "react";

import { ApiError } from "./client";

export type LoadState<T> =
  | { kind: "loading" }
  | { kind: "error"; error: ApiError | Error }
  | { kind: "loaded"; data: T };

type Keyed<T> = { key: string; state: LoadState<T> };

/**
 * Load data on mount and whenever `deps` change; `reload()` fetches again. Aborts on unmount.
 * State is keyed by the request, so a new request shows "loading" without a synchronous setState.
 */
export function useLoad<T>(
  load: (signal: AbortSignal) => Promise<T>,
  deps: readonly unknown[],
): [LoadState<T>, () => void] {
  const [version, setVersion] = useState(0);
  const key = JSON.stringify([...deps, version]);
  const [result, setResult] = useState<Keyed<T> | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    load(controller.signal)
      .then((data) => setResult({ key, state: { kind: "loaded", data } }))
      .catch((error: unknown) => {
        if (controller.signal.aborted) return;
        const err = error instanceof Error ? error : new Error("Unknown error");
        setResult({ key, state: { kind: "error", error: err } });
      });
    return () => controller.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- `key` captures the caller's dependencies
  }, [key]);

  const reload = useCallback(() => setVersion((v) => v + 1), []);
  const state: LoadState<T> = result && result.key === key ? result.state : { kind: "loading" };
  return [state, reload];
}
