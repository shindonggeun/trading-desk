import { useEffect, useRef } from "react";
import { api, ApiError, type SectorMoverKey, type SectorMoverRow, type SectorMovers } from "../api";
import { staleText, usePoll } from "../poll";

/** #740 섹터 급등락 주범(#680 GET /api/sectors/movers) — 미국 반도체 · 나스닥100 · 국장 반도체의 오늘 등락과,
 *  지수가 ±2% 넘게 움직인 날이면 가장 크게 끈 3종목(등락 · 기여 %p · 원인). 조용한 날은 가장 크게 끈 한 종목만 작게. */
const KEYS: SectorMoverKey[] = ["sox", "ndx", "kr_semi"];
const POLL_MS = 5 * 60_000;      // 서버 알림 작업과 같은 5분
/** 푸시(#/sectors?movers)로 열리면 이 카드로 내려간다 */
const wantsCard = () => /^#\/sectors[^?]*\?(?:.*&)?movers(?:[=&]|$)/.test(window.location.hash);

/** 부호를 붙인 %, 예: "+2.4%" · "−10.0%" (서버 문장과 같은 − 기호) */
export function signedPct(x: number, digits = 1): string {
  const s = Math.abs(x).toFixed(digits);
  return `${x > 0 ? "+" : x < 0 ? "−" : ""}${s}%`;
}
const toneOf = (x: number) => (x > 0 ? "up" : x < 0 ? "down" : "");

/** 세 섹터를 따로 읽는다 — 하나가 실패해도 나머지는 보인다. 다 실패하면 오류 */
async function loadAll(): Promise<(SectorMovers | null)[]> {
  const got = await Promise.allSettled(KEYS.map((k) => api.sectorMovers(k)));
  const failed = got.find((g): g is PromiseRejectedResult => g.status === "rejected");
  if (failed && got.every((g) => g.status === "rejected")) throw failed.reason;
  return got.map((g) => (g.status === "fulfilled" ? g.value : null));
}

/** 그 섹터를 움직인 방향의 종목들 — 내리면 끌어내린 쪽, 오르면 끌어올린 쪽 */
export const driversOf = (m: SectorMovers): SectorMoverRow[] => (m.move_pct < 0 ? m.down : m.up);

function Driver({ r }: { r: SectorMoverRow }) {
  return (
    <li>
      <span className="sm-name">{r.name}</span>{" "}
      <span className={`num ${toneOf(r.return_pct)}`}>{signedPct(r.return_pct)}</span>
      <span className="muted small"> 기여 {signedPct(r.contribution_pp, 2)}p{r.cause ? ` · ${r.cause}` : ""}</span>
    </li>
  );
}

function SectorRow({ m }: { m: SectorMovers }) {
  const falling = m.move_pct < 0;
  const who = driversOf(m);
  return (
    <li className={`sm-row${m.big ? " sm-big" : ""}`}>
      <div className="sm-head">
        <span className="sm-label">{m.label}</span>
        {m.date ? <strong className={`num ${toneOf(m.move_pct)}`}>{signedPct(m.move_pct)}</strong> : <strong className="num">—</strong>}
        {m.big && <span className={`sm-tag ${falling ? "down" : "up"}`}>{falling ? "▼ 급락 주범" : "▲ 급등 주역"}</span>}
        {m.date && <span className="muted small">{m.market === "US" ? "미장" : "국장"} {m.date.slice(5).replace("-", "/")}</span>}
      </div>
      {m.big && who.length > 0 ? (
        <ol className="sm-who">{who.map((r) => <Driver key={r.ticker} r={r} />)}</ol>
      ) : who[0] ? (
        <p className="muted small sm-quiet">
          가장 크게 {falling ? "끌어내린" : "끌어올린"} 종목: {who[0].name} {signedPct(who[0].return_pct)}{" "}
          (기여 {signedPct(who[0].contribution_pp, 2)}p)
        </p>
      ) : (
        <p className="muted small sm-quiet">{m.date ? "구성 종목 등락을 아직 읽지 못했습니다." : "지수 등락을 아직 읽지 못했습니다."}</p>
      )}
    </li>
  );
}

export default function SectorMoversCard() {
  const poll = usePoll("sector-movers", loadAll, POLL_MS);
  const { data, error, failing } = poll;
  const box = useRef<HTMLElement>(null);
  const jumped = useRef(false);
  useEffect(() => {   // 푸시로 열렸으면 한 번 이 카드로 — 주소가 바뀌어 다시 들어와도
    const on = () => { jumped.current = false; };
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  useEffect(() => {
    if (jumped.current || !box.current || !wantsCard()) return;
    jumped.current = true;
    box.current.scrollIntoView({ block: "start" });
  });
  if (error instanceof ApiError && error.status === 404 && !data) return null;   // 서버에 #680 이 없으면 숨김
  const rows = (data ?? []).filter((m): m is SectorMovers => m !== null);
  const asOf = rows.find((m) => m.weights_as_of)?.weights_as_of;
  return (
    <section ref={box} id="sector-movers" className="card sector-movers" aria-label="오늘 섹터 급등락 주범">
      <h3>오늘 섹터 급등락 주범</h3>
      {!data && !failing && <p className="muted small">불러오는 중…</p>}
      {!data && failing && <p className="error small" role="alert">섹터 등락을 불러오지 못했습니다.</p>}
      {rows.length > 0 && <ul className="sm-list">{rows.map((m) => <SectorRow key={m.sector} m={m} />)}</ul>}
      {data && rows.length < KEYS.length && <p className="muted small">일부 섹터를 불러오지 못했습니다.</p>}
      <p className="muted small sm-note">
        기여도(%p) = 비중 × 등락. 지수가 ±2% 이상 움직이면 큰 날로 보고 그 방향으로 가장 크게 움직인 3종목을 적습니다.
        {asOf ? ` 비중은 ${asOf} 기준 근사값입니다.` : ""}
        {data && staleText(poll) ? ` ${staleText(poll)}` : ""}
      </p>
    </section>
  );
}
