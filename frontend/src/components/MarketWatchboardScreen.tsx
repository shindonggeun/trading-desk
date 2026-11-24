import { useEffect, useState } from "react";
import type { WatchboardGroup, WatchboardResponse } from "../marketWatchboard";
import MarketWatchboardPanel from "./MarketWatchboardPanel";

export type LoadWatchboard = (group: WatchboardGroup, signal: AbortSignal) => Promise<WatchboardResponse>;

type LoadResult = {
  group: WatchboardGroup;
  data: WatchboardResponse | null;
  error: string | null;
};

export default function MarketWatchboardScreen({ load }: { load: LoadWatchboard }) {
  const [group, setGroup] = useState<WatchboardGroup>("semiconductors");
  const [result, setResult] = useState<LoadResult | null>(null);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    load(group, controller.signal).then(
      (data) => {
        if (controller.signal.aborted) return;
        const valid = data?.group === group;
        setResult({ group, data: valid ? data : null, error: valid ? null : "요청한 관심군과 응답이 다릅니다." });
      },
      () => {
        if (!controller.signal.aborted) setResult({ group, data: null, error: "시장 모니터를 불러오지 못했습니다." });
      },
    );
    return () => controller.abort();
  }, [load, group, attempt]);

  const active = result?.group === group ? result : null;
  return (
    <MarketWatchboardPanel
      group={group}
      onGroupChange={(next) => {
        if (next !== group) {
          setResult(null);
          setGroup(next);
        }
      }}
      data={active?.data ?? null}
      loading={active === null}
      error={active?.error ?? null}
      onRetry={() => { setResult(null); setAttempt((value) => value + 1); }}
    />
  );
}
