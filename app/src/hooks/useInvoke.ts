// Unified Tauri command wrapper.
// Every component uses this hook, so error handling, loading state, and
// logging are uniform across the app. Without it, each component would
// reinvent try/catch patterns and forgetting one call would crash the UI.

import { useCallback, useEffect, useRef, useState } from "react";
import { invoke } from "@tauri-apps/api/core";

export interface UseInvokeResult<T> {
  data: T | null;
  loading: boolean;
  error: string | null;
  refetch: () => Promise<void>;
}

export function useInvoke<T>(
  command: string,
  args: Record<string, unknown> = {},
  options: { immediate?: boolean; deps?: unknown[] } = {}
): UseInvokeResult<T> {
  const { immediate = true, deps = [] } = options;
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  const refetch = useCallback(async () => {
    if (!mountedRef.current) return;
    setLoading(true);
    setError(null);
    try {
      const result = await invoke<T>(command, args);
      if (mountedRef.current) setData(result);
    } catch (e) {
      const msg =
        typeof e === "string" ? e : (e as { message?: string })?.message ?? "Unknown error";
      if (mountedRef.current) setError(msg);
      console.error(`[useInvoke] ${command} failed:`, e);
    } finally {
      if (mountedRef.current) setLoading(false);
    }
  }, [command, JSON.stringify(args)]);

  useEffect(() => {
    if (immediate) refetch();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refetch, ...deps]);

  return { data, loading, error, refetch };
}
