import { useEffect, useRef } from "react";
import {
  CHANNEL_STATE_KO, channelTitle, NOT_ADVICE, rateText, TF_KO, TREND_STATE_KO, trendTitle,
} from "../channelLines";
import { price } from "../format";
import type { LinesReport, ParallelChannel, ShortTrendline } from "../types";

export type LinePick = { kind: "channel" | "trend"; id: string };

const when = (iso: string | null | undefined) => {
  const t = iso ? new Date(Date.parse(iso) + 9 * 3600_000) : null;
  if (!t || Number.isNaN(t.getTime())) return "—";
  const p = (n: number) => String(n).padStart(2, "0");
  return `${p(t.getUTCMonth() + 1)}-${p(t.getUTCDate())} ${p(t.getUTCHours())}:${p(t.getUTCMinutes())}`;
};
const gap = (from: number | null, to: number) => (from == null || !(from > 0) ? "" : ` (${to >= from ? "+" : ""}${(((to - from) / from) * 100).toFixed(2)}%)`);

function ChannelBody({ c, ref0 }: { c: ParallelChannel; ref0: number | null }) {
  const lower = rateText(c.hit_rate?.lower, c.timeframe);
  const upper = rateText(c.hit_rate?.upper, c.timeframe);
  return (
    <>
      <p className="small">{c.explain}</p>
      <dl className="line-card-facts small">
        <div><dt>지금</dt><dd>{CHANNEL_STATE_KO[c.state]} · 하단 0 ~ 상단 1 중 {c.position.toFixed(2)}</dd></div>
        <div><dt>상단</dt><dd>{price(c.upper.end.price)}{gap(ref0, c.upper.end.price)}</dd></div>
        <div><dt>하단</dt><dd>{price(c.lower.end.price)}{gap(ref0, c.lower.end.price)}</dd></div>
        <div><dt>바깥 선</dt><dd>{price(c.outer_upper.end.price)} · {price(c.outer_lower.end.price)} <span className="muted">(점선, 폭 한 칸 밖)</span></dd></div>
        <div><dt>폭</dt><dd>{c.width_pct.toFixed(1)}% · {c.width_atr.toFixed(1)} ATR</dd></div>
        <div><dt>시작</dt><dd>{when(c.start_at)} · 위 {c.touches.upper.length}번 · 아래 {c.touches.lower.length}번 닿음</dd></div>
        {c.broke_at && <div><dt>벗어남</dt><dd>{when(c.broke_at)} 닫힌 봉부터</dd></div>}
      </dl>
      {(lower || upper) ? (
        <ul className="line-card-rates small">
          {lower && <li><strong>하단</strong> {lower}</li>}
          {upper && <li><strong>상단</strong> {upper}</li>}
        </ul>
      ) : <p className="muted small">이 시간봉은 과거 맞은 비율을 아직 재지 않았어요.</p>}
    </>
  );
}

function TrendBody({ t, ref0 }: { t: ShortTrendline; ref0: number | null }) {
  const r = rateText(t.hit_rate, t.timeframe);
  return (
    <>
      <p className="small">{t.explain}</p>
      <dl className="line-card-facts small">
        <div><dt>지금</dt><dd>{TREND_STATE_KO[t.state]}{t.distance_atr != null ? ` · 선에서 ${Math.abs(t.distance_atr).toFixed(1)} ATR ${t.distance_atr < 0 ? "바깥" : "안쪽"}` : ""}</dd></div>
        <div><dt>선 값</dt><dd>{price(t.end.price)}{gap(ref0, t.end.price)}</dd></div>
        <div><dt>앵커</dt><dd>{t.anchors.map((a) => `${when(a.at)} ${price(a.price)}`).join(" → ")}</dd></div>
        {t.broken_at && <div><dt>이탈</dt><dd>{when(t.broken_at)}{t.broken_price != null ? ` · ${price(t.broken_price)}` : ""}</dd></div>}
      </dl>
      {r ? <p className="small line-card-rates">{r}</p>
        : <p className="muted small">{t.state === "broken" && !t.fresh_break ? "오래전 이탈이라 따라감 비율을 보이지 않아요." : "이 시간봉은 과거 맞은 비율을 아직 재지 않았어요."}</p>}
    </>
  );
}

/**
 * #612 차트의 평행 채널 · 짧은 추세선(#611)을 누르면 — 쉬운 말 설명, 지금 위치, 선 값, 과거 맞은 비율(같은 기울기 · 거리의
 * 아무 선과 나란히). 백테스트에서 선이 아무 선보다 낫지 않았으니 비율을 그대로 보이고 "추천 아님" 을 붙인다. Esc · 닫기로 닫는다.
 */
export default function ChannelLineCard({ rep, pick, onClose }: { rep: LinesReport; pick: LinePick; onClose: () => void }) {
  const closeRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    closeRef.current?.focus({ preventScroll: true });
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape" || e.defaultPrevented) return;
      const t = e.target as HTMLElement | null;
      if (t && t.closest("input, textarea, select, [contenteditable=true], dialog[open]")) return;
      e.preventDefault();
      onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  const c = pick.kind === "channel" ? rep.channels.find((x) => x.id === pick.id) : undefined;
  const t = pick.kind === "trend" ? rep.trendlines.find((x) => x.id === pick.id) : undefined;
  if (!c && !t) return null;
  return (
    <section className="gap-card line-card" aria-label="채널 · 추세선 설명">
      <header className="gap-card-head">
        <strong>{c ? channelTitle(c) : trendTitle(t!)}</strong>
        <span className="muted small">{TF_KO[rep.timeframe] ?? rep.timeframe} · 기준 {when(rep.as_of)}{rep.fake ? " · 화면 확인용 가짜" : ""}</span>
        <button ref={closeRef} type="button" className="link small" onClick={onClose} aria-label="채널 · 추세선 카드 닫기">닫기</button>
      </header>
      {c ? <ChannelBody c={c} ref0={rep.reference_price} /> : <TrendBody t={t!} ref0={rep.reference_price} />}
      <p className="muted small">{rep.note || NOT_ADVICE} 과거 비율은 그 선과 기울기 · 거리가 같은 아무 선(기준선)과 함께 봐 주세요 — 대부분 비슷했어요.</p>
    </section>
  );
}
