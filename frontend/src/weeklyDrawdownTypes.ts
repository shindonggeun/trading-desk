export type WeeklyEvidenceKey = "bollinger" | "rsi" | "channel" | "fib";

export interface WeeklyEvidence {
  /** RSI 값 또는 연구 기준의 가격 수준. */
  value: number | null;
  state: "near" | "oversold" | "below" | "normal" | "missing";
  reason: string | null;
}

export interface WeeklyCheck {
  state: "watching" | "confirmed" | "invalidated" | "not_confirmed" | "missing";
  condition: string | null;
  invalidation: string | null;
}

/** #896 표시 초안용 입력. 실제 차트 읽기 응답은 확정 후 이 형태로 받는다. */
export type WeeklyDrawdownObservation =
  | { status: "unavailable"; reason: string | null }
  | {
      status: "available";
      week: {
        /** 뉴욕 거래 주의 시작일(YYYY-MM-DD). */
        start: string | null;
        source: string | null;
        candle: "closed" | "forming" | "unknown";
      };
      current: {
        price: number | null;
        source: string | null;
        asOf: string | null;
      };
      evidence: Record<WeeklyEvidenceKey, WeeklyEvidence>;
      overlap: {
        state: "overlap" | "none" | "missing";
        low: number | null;
        high: number | null;
        basis: WeeklyEvidenceKey[];
      };
      rebound: WeeklyCheck;
      recovery: WeeklyCheck;
    };
