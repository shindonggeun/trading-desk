export type WeeklyEvidenceKey = "bollinger" | "rsi" | "channel" | "fib";

interface WeeklyReason {
  reason: string | null;
  reasonCode?: string | null;
}

export interface WeeklyEvidence extends WeeklyReason {
  /** RSI 값 또는 연구 기준의 가격 수준. */
  value: number | null;
  state: "near" | "oversold" | "below" | "normal" | "missing";
  /** 차트 봉 종가에 대한 가격 근거의 서버 계산 이격률. */
  distancePct?: number | null;
  method?: string;
  anchorWeeks?: string[];
}

export interface WeeklyCheck extends Partial<WeeklyReason> {
  state: "watching" | "confirmed" | "invalidated" | "not_confirmed" | "missing";
  condition: string | null;
  invalidation: string | null;
  source?: string | null;
  anchorWeek?: string | null;
  decisionWeek?: string | null;
  windowWeeks?: number;
  threshold?: number | null;
  invalidationLevel?: number | null;
}

interface WeeklyObservationMetadata {
  observedAt?: string;
  usage?: "research_only";
}

/** 기본 chart-read 요청의 주봉 관측. 메타데이터 없는 표시 예시도 받을 수 있다. */
export type WeeklyDrawdownObservation = WeeklyObservationMetadata &
  (
    | ({ status: "unavailable" } & WeeklyReason)
    | {
        status: "available";
        assumptions?: {
          series: string;
          referenceSeries: string;
          rollover: "unknown";
          originalSettings: "unknown";
          originalAnchors: "unknown";
        };
        week: {
          /** 뉴욕 거래 주의 시작일(YYYY-MM-DD). */
          start: string | null;
          source: string | null;
          candle: "closed" | "forming" | "unknown";
          closedAt?: string;
          fetchedAt?: string;
        };
        current: Partial<WeeklyReason> & {
          price: number | null;
          source: string | null;
          /** 비교에 사용한 봉의 시작 라벨. */
          asOf: string | null;
          fetchedAt?: string | null;
          timeframe?: string | null;
          kind?: "chart_bar_close";
        };
        evidence: Record<WeeklyEvidenceKey, WeeklyEvidence>;
        overlap: Partial<WeeklyReason> & {
          state: "overlap" | "none" | "missing";
          low: number | null;
          high: number | null;
          basis: WeeklyEvidenceKey[];
        };
        rebound: WeeklyCheck;
        recovery: WeeklyCheck;
      }
  );
