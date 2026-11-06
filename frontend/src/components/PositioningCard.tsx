import { useEffect, useState } from "react";
import { api } from "../api";
import type { CreditView, Positioning } from "../types";
import PositioningView, { CreditSummary, positioningBadges } from "./PositioningView";

const US = /^[A-Z]{1,6}(?:[.-][A-Z])?$/;
const KOREAN = /\.(KS|KQ)$/i;

/**
 * 분석 화면 요약 탭의 포지션 카드(#158): 미국 종목은 공매도 · 옵션 포지션(#154), 국내 종목은 빚투 · 예탁금(#155).
 * 둘 다 아니면(지수 · 선물 · 환율) 그리지 않는다. 판정 배지를 먼저, 숫자는 크게.
 */
export default function PositioningCard({ ticker }: { ticker: string }) {
  const us = US.test(ticker);
  const korean = KOREAN.test(ticker);
  const [p, setP] = useState<Positioning | null | undefined>(undefined);
  const [credit, setCredit] = useState<CreditView | null | undefined>(undefined);
  useEffect(() => {
    let alive = true;
    setP(undefined);
    setCredit(undefined);
    if (us) api.positioning(ticker).then((v) => alive && setP(v), () => alive && setP(null));
    if (korean) api.credit(30).then((v) => alive && setCredit(v), () => alive && setCredit(null));
    return () => { alive = false; };
  }, [ticker, us, korean]);
  if (!us && !korean) return null;
  const badges = positioningBadges(p);
  return (
    <section className="card positioning-card" aria-label={us ? "공매도 · 옵션 포지션" : "빚투 · 예탁금"}>
      <div className="pc-head">
        <h3>{us ? "공매도 · 옵션 포지션" : "국내 빚투 · 예탁금"}</h3>
        {badges.length > 0 && (
          <span className="sector-badges">
            {badges.map((b) => <span key={b.text} className={`sector-badge ${b.tone}`}>{b.text}</span>)}
          </span>
        )}
      </div>
      {us ? <PositioningView p={p} /> : <CreditSummary c={credit} />}
      {us && <p className="muted small pv-note">가격 차트의 "표시 → 옵션 벽 · 맥스 페인"으로 콜 벽 · 풋 벽을 차트에 겹쳐 봅니다.</p>}
    </section>
  );
}
