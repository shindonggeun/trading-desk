import { type ReactNode, useContext, useState } from "react";
import type { LineSignal } from "../api";
import { chartHref, LineWatchScopeContext } from "../lineSignals";

/** 과거 신호 가격은 현재 인증 화면 수명 동안만 보관한다. 주소에는 불투명 참조만 남긴다. */
export default function LineSignalLink({ signal, children }: { signal: LineSignal; children: ReactNode }) {
  const scope = useContext(LineWatchScopeContext);
  const [reference] = useState(() => Array.from(crypto.getRandomValues(new Uint32Array(4)), n => n.toString(16)).join("-"));
  return (
    <a href={chartHref(signal, scope ? reference : undefined, signal.timeframe, signal.signal_id)} onClick={() => {
      if (!scope) return;
      const { ticker, timeframe, line_price: price, at } = signal;
      scope.chartAlerts.delete(reference);
      scope.chartAlerts.set(reference, { ticker, timeframe, price, at });
      if (scope.chartAlerts.size > 32) {
        const oldest = scope.chartAlerts.keys().next().value;
        if (oldest !== undefined) scope.chartAlerts.delete(oldest);
      }
    }}>{children}</a>
  );
}
