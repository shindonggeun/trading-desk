import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { krwShort, price } from "../format";
import type { FuturesFlow, FuturesInvestor } from "../types";
import FuturesHistoryView from "./FuturesHistoryView";

// 시장 수급의 "선물" 보기(#161): 코스피200 근월물의 투자자별 순매수(계약 · 금액), 미결제약정, 베이시스,
// 외국인 현물 · 선물 엇갈림. 막대 색은 현물 보기와 같은 극성(순매수 빨강 · 순매도 파랑).

const CLASSES: { key: "foreign" | "individual" | "institution"; label: string }[] = [
  { key: "foreign", label: "외국인" }, { key: "institution", label: "기관" }, { key: "individual", label: "개인" },
];
// 서버 캐시가 장중 30초 · 장 밖 10분이라 그보다 자주 부르지 않는다
const REFRESH_LIVE_MS = 30_000;
const REFRESH_IDLE_MS = 300_000;

const signed = (v: number | null | undefined, digits = 2) =>
  v == null ? "—" : `${v > 0 ? "+" : ""}${v.toLocaleString("ko-KR", { maximumFractionDigits: digits })}`;
const contracts = (v: number | null | undefined) => (v == null ? "—" : `${signed(v, 0)}계약`);
const valueMn = (v: number | null | undefined) => (v == null ? "—" : krwShort(v * 1_000_000));

const STRUCTURE: Record<string, string> = {
  contango: "콘탱고(선물 > 현물)", backwardation: "백워데이션(선물 < 현물)", flat: "선물 = 현물",
};
const SPLIT: Record<string, string> = {
  spot_buy_futures_sell: "현물은 사고 선물은 판다 — 하락 대비(헤지)일 수 있음",
  spot_sell_futures_buy: "현물은 팔고 선물은 산다 — 반등 베팅일 수 있음",
};

function Bar({ label, side, max }: { label: string; side: FuturesInvestor | undefined; max: number }) {
  if (!side) return null;
  const net = side.net_contracts ?? side.net_value_mn ?? 0;   // 계약 수가 없으면 금액으로 방향만
  const share = max > 0 && side.net_contracts != null ? Math.min(Math.abs(side.net_contracts) / max, 1) * 50 : 0;
  const buying = net > 0;
  const word = net === 0 ? "보합" : buying ? "순매수" : "순매도";
  return (
    <li className="flow-row">
      <span className="flow-name">{label}</span>
      <span className="flow-track" aria-hidden>
        <span className="flow-zero" />
        {share > 0 && (
          <span className={`flow-bar ${buying ? "flow-buy" : "flow-sell"}`}
            style={buying ? { left: "50%", width: `${share}%` } : { right: "50%", width: `${share}%` }} />
        )}
      </span>
      <span className="flow-net">
        <strong>{contracts(side.net_contracts)}</strong> <span className="muted small">{word}</span>
      </span>
      <span className="flow-sides muted small">금액 {valueMn(side.net_value_mn)}</span>
    </li>
  );
}

export default function FuturesFlowView() {
  const [data, setData] = useState<FuturesFlow | null>(null);
  const [failed, setFailed] = useState<string | null>(null);
  const liveRef = useRef(true);
  const seq = useRef(0);

  useEffect(() => {
    let alive = true;
    let timer: number | undefined;
    const visible = () => document.visibilityState === "visible";
    const schedule = () => {
      window.clearInterval(timer);
      timer = window.setInterval(() => visible() && load(), liveRef.current ? REFRESH_LIVE_MS : REFRESH_IDLE_MS);
    };
    const load = () => {
      const mine = ++seq.current;
      api.marketFutures().then((d) => {
        if (!alive || mine !== seq.current) return; // 겹친 요청 중 늦게 온 이전 응답
        // 시세를 못 받은 응답은 마지막 값을 두고 알림만(처음이면 그 응답을 그대로 보여 준다)
        setData((old) => (d.price == null && old?.price ? old : d));
        setFailed(d.price == null && d.supported ? d.error : null);
        if (d.market_open !== liveRef.current) {
          liveRef.current = d.market_open;
          schedule();
        }
      }, (e) => alive && mine === seq.current && setFailed(e instanceof Error ? e.message : "불러오지 못했습니다."));
    };
    const onVisible = () => visible() && load();
    load();
    schedule();
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      alive = false;
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, []);

  const p = data?.price;
  const inv = data?.investors;
  const max = inv ? Math.max(...CLASSES.map((c) => Math.abs(inv[c.key]?.net_contracts ?? 0)), 1) : 1;
  const split = data?.split;
  const tone = (v: number | null | undefined) => (v == null || v === 0 ? "" : v > 0 ? "up" : "down");

  return (
    <div className="fut">
      {failed && <p className="error small" role="alert">{p ? "갱신하지 못했습니다 — 마지막 값을 보여 줍니다." : failed}</p>}
      {data && !data.supported && <p className="muted small">{data.error}</p>}
      {!data && !failed && <p className="muted small">불러오는 중…</p>}

      {p && data && (
        <>
          <div className="fut-head">
            <div>
              <strong className="fut-price">{price(p.price)}</strong>{" "}
              <span className={`small ${tone(p.change)}`}>{signed(p.change)}{p.change_pct != null && ` (${signed(p.change_pct)}%)`}</span>
            </div>
            <span className="muted small">
              코스피200 선물 {p.code} · 만기 {p.expiry.slice(5).replace("-", "/")} (D-{p.days_left})
              {data.market_open ? "" : " · 장 마감"}
            </span>
          </div>
          <dl className="fut-grid small">
            <div>
              <dt>미결제약정</dt>
              <dd>{p.open_interest == null ? "—" : `${p.open_interest.toLocaleString("ko-KR")}계약`}{" "}
                <span className={`muted ${tone(p.open_interest_change)}`}>({contracts(p.open_interest_change)})</span></dd>
            </div>
            <div>
              <dt>시장 베이시스</dt>
              <dd>{signed(p.market_basis)} <span className="muted">{p.structure ? STRUCTURE[p.structure] : ""}</span></dd>
            </div>
            <div>
              <dt>이론 베이시스</dt>
              <dd>{signed(p.theory_basis)}{p.theory_price != null && <span className="muted"> · 이론가 {price(p.theory_price)}</span>}</dd>
            </div>
          </dl>

          {split?.split && split.kind && (
            <p className="fut-split small" role="status">
              <span className="pill pill-warn">엇갈림</span>{" "}
              외국인 현물 <strong>{krwShort(split.spot_net)}</strong> · 선물 <strong>{contracts(split.futures_net_contracts)}</strong>
              <span className="muted"> — {SPLIT[split.kind]}</span>
            </p>
          )}
          {split && !split.split && split.reason === "spot_not_today" && (
            <p className="muted small">현물 수급에 아직 오늘 기록이 없어 외국인 현물 · 선물을 비교하지 않습니다.</p>
          )}

          {inv ? (
            <ul className="flow-bars" aria-label="코스피200 선물 투자자별 순매수">
              {CLASSES.map((c) => <Bar key={c.key} label={c.label} side={inv[c.key]} max={max} />)}
            </ul>
          ) : (
            <p className="muted small">{data.investors_error ?? "투자자별 동향이 없습니다."}</p>
          )}
          <p className="muted small flow-note">
            오늘 장중 누적 순매수(계약, 금액은 {data.investors_unit.value}). 선물은 현물보다 먼저 방향을 거는 곳이라
            외국인 선물 순매수 · 미결제약정 증감을 함께 봅니다. 출처 한국투자증권 · {data.fetched_at.slice(11, 16)} 기준
          </p>
        </>
      )}
      {/* #255 일자별(간결) · 장중(자세히) 기록 — 지금 시세를 못 받아도 기록은 보인다 */}
      <FuturesHistoryView />
    </div>
  );
}
