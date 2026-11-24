"use client";
import { useCallback, useEffect, useRef, useState } from "react";

export class ApiClientError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
    public details?: { field: string; msg: string }[] | null,
  ) {
    super(message);
  }
}

interface Envelope<T> {
  status: "ok" | "error";
  data?: T;
  warnings?: string[];
  error?: { code: string; message: string; details?: { field: string; msg: string }[] | null };
}

/** Call the API through the server-side proxy; returns the unwrapped `data`. */
export async function api<T>(method: "GET" | "POST" | "PATCH", path: string, body?: unknown): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`/api/gtmos/${path.replace(/^\/+/, "")}`, {
      method,
      headers: body === undefined ? undefined : { "content-type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
      cache: "no-store",
    });
  } catch {
    throw new ApiClientError(0, "network", "Could not reach the server");
  }
  let env: Envelope<T> | null = null;
  try {
    env = (await res.json()) as Envelope<T>;
  } catch {
    /* non-JSON error body */
  }
  if (!res.ok || !env || env.status !== "ok") {
    throw new ApiClientError(
      res.status,
      env?.error?.code ?? "http_error",
      env?.error?.message ?? `Request failed (${res.status})`,
      env?.error?.details,
    );
  }
  return env.data as T;
}

export function errorText(e: unknown): string {
  if (e instanceof ApiClientError) {
    const detail = e.details?.map((d) => `${d.field}: ${d.msg}`).join("; ");
    return detail ? `${e.message} (${detail})` : e.message;
  }
  return e instanceof Error ? e.message : "Something went wrong";
}

export interface Loaded<T> {
  data: T | null;
  error: ApiClientError | null;
  loading: boolean;
  reload: () => Promise<void>;
  set: (v: T) => void;
}

/** Fetch on mount (and when `path` changes). `path === null` skips the request. */
export function useApi<T>(path: string | null): Loaded<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<ApiClientError | null>(null);
  const [loading, setLoading] = useState(path !== null);
  const seq = useRef(0);

  const load = useCallback(async () => {
    if (path === null) return;
    const mine = ++seq.current;
    setLoading(true);
    try {
      const v = await api<T>("GET", path);
      if (mine === seq.current) {
        setData(v);
        setError(null);
      }
    } catch (e) {
      if (mine === seq.current) setError(e instanceof ApiClientError ? e : new ApiClientError(0, "error", errorText(e)));
    } finally {
      if (mine === seq.current) setLoading(false);
    }
  }, [path]);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void load();
  }, [load]);

  return { data, error, loading, reload: load, set: setData };
}
