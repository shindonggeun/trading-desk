import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, ApiError } from "./api";
import type { NqWeeklyOversoldSaved, NqWeeklyOversoldSettings, NqWeeklyOversoldView } from "./nqWeeklyOversold";

type Scope = object;
type RequestScope = {
  scope: Scope;
  lifetime: AbortController;
  reader?: AbortController;
  writing: boolean;
  minRevision?: number;
};
type Snapshot = {
  scope: Scope | null;
  view: NqWeeklyOversoldView | null;
  loading: boolean;
  readFailed: boolean;
  saving: boolean;
  saveFailed: boolean;
  saved: NqWeeklyOversoldSaved | null;
};
const EMPTY: Snapshot = { scope: null, view: null, loading: false, readFailed: false, saving: false, saveFailed: false, saved: null };

export function useNqWeeklyOversold(active: boolean) {
  const scope = useMemo<Scope | null>(() => active ? {} : null, [active]);
  const session = useRef<RequestScope | null>(null);
  const [snapshot, setSnapshot] = useState(EMPTY);
  const state = snapshot.scope === scope ? snapshot : { ...EMPTY, loading: active };

  const read = useCallback(async (request: RequestScope) => {
    request.reader?.abort();
    const reader = new AbortController();
    request.reader = reader;
    setSnapshot(current => ({ ...current, loading: true, readFailed: false }));
    const valid = () => session.current === request && !reader.signal.aborted;
    try {
      const result = await api.nqWeeklyOversold(reader.signal);
      if (!valid()) return;
      if (result.can_edit && request.minRevision !== undefined && result.revision < request.minRevision) {
        setSnapshot(current => ({ ...current, view: null, loading: false, readFailed: true }));
        return;
      }
      // 비소유자나 새 소유자 범위의 revision은 이전 소유자의 저장 버전과 비교할 수 없다.
      delete request.minRevision;
      setSnapshot(current => ({
        ...current, view: result, loading: false,
        saved: result.can_edit && current.saved?.revision === result.revision ? current.saved : null,
      }));
    } catch {
      if (valid()) setSnapshot(current => ({ ...current, view: null, loading: false, readFailed: true }));
    } finally {
      if (request.reader === reader) delete request.reader;
    }
  }, []);

  useEffect(() => {
    if (!scope) return;
    const request: RequestScope = { scope, lifetime: new AbortController(), writing: false };
    session.current = request;
    setSnapshot({ ...EMPTY, scope, loading: true });
    void read(request);
    return () => {
      session.current = null;
      request.reader?.abort();
      request.lifetime.abort();
    };
  }, [scope, read]);

  const refresh = () => {
    const request = session.current;
    if (request && request.scope === scope && !request.writing) void read(request);
  };

  const save = async (settings: NqWeeklyOversoldSettings): Promise<boolean> => {
    const request = session.current;
    if (!request || request.scope !== scope || request.reader || request.writing || !state.view?.can_edit) return false;
    request.writing = true;
    setSnapshot(current => ({ ...current, saving: true, saveFailed: false, saved: null }));
    const valid = () => session.current === request && !request.lifetime.signal.aborted;
    try {
      const result = await api.saveNqWeeklyOversold(settings, request.lifetime.signal);
      if (!valid()) return false;
      request.minRevision = result.revision;
      // PUT은 규칙만 응답한다. 관찰 결과가 새 규칙을 사용하기 전에는 종전 마커를 감춘다.
      setSnapshot(current => ({ ...current, view: null, saved: result, saving: false, loading: true }));
      void read(request);
      return true;
    } catch (error) {
      if (valid()) {
        const forbidden = error instanceof ApiError && error.status === 403;
        setSnapshot(current => ({
          ...current, view: forbidden ? null : current.view, saving: false, saveFailed: true,
        }));
        if (forbidden) void read(request);
      }
      return false;
    } finally {
      request.writing = false;
    }
  };

  return { ...state, refresh, save };
}
