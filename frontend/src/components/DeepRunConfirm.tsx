import { type ReactNode, useEffect, useRef, useState } from "react";
import { api } from "../api";
import type { DeskConfig } from "../types";
import FavoriteStar from "./FavoriteStar";

/**
 * #539 상세 분석(에이전트 토론 → 타점) 시작 확인 칸 — 시장 칸(MarketBoard)과 차트 화면(ChartView)이 같이 쓴다.
 * 누르기만으로는 시작하지 않는다: 사용량 안내를 보고 "분석 시작"을 한 번 더 눌러야 run 을 만든다(#697 기간 · 성향은 고르지 않는다).
 * 열리면 포커스는 "취소"(Enter 를 한 번 더 눌러도 시작되지 않게), Esc 로 닫힌다.
 */
export default function DeepRunConfirm({ ticker, name, extra, onCancel }: {
  ticker: string;
  name: string;
  /** 확인 문장 아래 한 줄(시장 칸: 지지 · 저항) */
  extra?: ReactNode;
  onCancel: () => void;
}) {
  const [config, setConfig] = useState<DeskConfig | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const box = useRef<HTMLDivElement>(null);

  useEffect(() => {
    api.config().then(setConfig, () => {});
  }, []);

  useEffect(() => {
    box.current?.querySelector<HTMLButtonElement>("button.mb-cancel")?.focus();
    // 캡처 단계에서 먼저 받아 막는다 — 차트 화면의 크게 보기 Esc(화면 닫기)까지 가지 않게, 이 칸만 닫힌다
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      e.preventDefault();
      onCancel();
    };
    document.addEventListener("keydown", onKey, true);
    return () => document.removeEventListener("keydown", onKey, true);
  }, [ticker, onCancel]);


  async function start() {
    setBusy(true);
    setError(null);
    try {
      const run = await api.createRun({
        ticker,
        name,
        analysts: config?.analysts ?? ["market", "social", "news", "fundamentals"], // 재무는 서버가 지수 · 선물 · 환율에서 뺀다
        use_portfolio: false,
        output_language: "Korean",
      });
      window.location.hash = `#/runs/${run.id}`;
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setBusy(false);
    }
  }

  return (
    <div className="mb-confirm" id="mb-confirm" ref={box} role="group" aria-label="상세 분석 시작 확인">
      <p>
        <FavoriteStar ticker={ticker} name={name} /> <strong>{name}</strong> ({ticker}) 상세 분석을 시작할까요?
      </p>
      {extra}
      <p className="muted small">
        에이전트들이 토론해 진입 · 손절 · 목표가를 내고, 스캘핑 · 단타 · 짧은 스윙 · 스윙 네 기간을 함께 보여 줍니다. 보고서
        한국어 · 보유 종목 반영 안 함. 지수 · 선물 · 환율 · 코인은 재무 분석가 없이 진행합니다. 한 번에 수 분이 걸리고 구독 플랜
        사용량이 차감됩니다.
      </p>
      {error && <p className="error small">{error}</p>}
      <div className="mb-confirm-actions">
        <button type="button" disabled={busy} onClick={start}>
          {busy ? "등록 중…" : "분석 시작"}
        </button>
        <button type="button" className="secondary mb-cancel" disabled={busy} onClick={onCancel}>
          취소
        </button>
      </div>
    </div>
  );
}
