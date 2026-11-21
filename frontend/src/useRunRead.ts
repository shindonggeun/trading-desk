import { useCallback, useEffect, useId, useRef, useState } from "react";
import { api, ApiError, Unauthorized } from "./api";
import { usePoll } from "./poll";
import { isFullRun } from "./runRead";
import type { RunRead } from "./types";

type ReadScope = {
  id: string;
  controller: AbortController;
  requested: number;
  applied: number;
  full: boolean;
  epoch: number;
};

export function useRunRead(runId: string | null, pollQueue = false) {
  const key = useId();
  const [view, setView] = useState<{ id: string; response: RunRead | null; error: string | null; epoch: number }>();
  const scopeRef = useRef<ReadScope | null>(null);

  const read = useCallback(async () => {
    const scope = scopeRef.current;
    if (!runId || scope?.id !== runId || scope.controller.signal.aborted) return;
    const request = ++scope.requested;
    const { signal } = scope.controller;
    try {
      const response = await api.run(runId, signal);
      if (signal.aborted || request <= scope.applied) return;
      scope.applied = request;
      const full = isFullRun(response);
      if (full && !scope.full) scope.epoch++;
      scope.full = full;
      setView({ id: runId, response, error: null, epoch: scope.epoch });
    } catch (error) {
      if (signal.aborted || request <= scope.applied) return;
      const denied = error instanceof Unauthorized || (error instanceof ApiError && [403, 404].includes(error.status));
      if (scope.applied === 0 || denied) {
        scope.applied = request;
        scope.full = false;
        setView({ id: runId, response: null, error: String(error), epoch: scope.epoch });
      }
      throw error;
    }
  }, [runId]);

  useEffect(() => {
    if (!runId) return;
    const controller = new AbortController();
    scopeRef.current = { id: runId, controller, requested: 0, applied: 0, full: false, epoch: 0 };
    const recheck = () => {
      if (document.visibilityState === "visible") void read().catch(() => {});
    };
    void read().catch(() => {});
    document.addEventListener("visibilitychange", recheck);
    return () => {
      controller.abort();
      document.removeEventListener("visibilitychange", recheck);
    };
  }, [read, runId]);

  const current = view?.id === runId ? view : undefined;
  const response = current?.response;
  const recheck = response && (response.status === "progress" || (pollQueue && response.status === "queued"));
  usePoll(recheck ? `run-read:${runId}:${key}` : null, read, 5000, { delay: 5000 });

  // Revocation applies before React unmounts the body. Old stream callbacks cannot reuse a later grant.
  const canRead = useCallback((epoch: number) => {
    const scope = scopeRef.current;
    return !!scope?.full && scope.id === runId && !scope.controller.signal.aborted && scope.epoch === epoch;
  }, [runId]);
  return { response, error: current?.error, epoch: current?.epoch ?? 0, read, canRead };
}
