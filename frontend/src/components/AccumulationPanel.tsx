import { price } from "../format";
import type { AccumulationBand, AccumulationViewKey, ChartRead } from "../types";

const ACCUMULATION_VIEWS = {
  short: "단기",
  mid: "중기",
  long: "장기",
} satisfies Record<AccumulationViewKey, string>;

const VIEW_KEYS: AccumulationViewKey[] = ["short", "mid", "long"];

const signed = (value: number, digits = 2) =>
  (value > 0 ? "+" : "") + value.toLocaleString("ko-KR", { maximumFractionDigits: digits });

const pct = (value: number | null) => value == null ? "—" : value.toFixed(1) + "%";

function bandLabel(band: AccumulationBand) {
  return band.role === "accumulation" ? "매집 후보" : "매물 저항";
}

export default function AccumulationPanel({ read, selected, onSelect }: {
  read: ChartRead;
  selected: AccumulationViewKey;
  onSelect: (view: AccumulationViewKey) => void;
}) {
  const accumulation = read.accumulation;
  const view = accumulation?.[selected];
  const legs = read.legs?.[selected];
  const stats = view?.error === undefined ? view?.stats : null;
  const bands = view ? [...view.below, ...view.above] : [];
  if (accumulation === undefined) return null; // Older chart-read payloads predate #751.

  return (
    <section className="accumulation-read" aria-label="관점별 매집 후보">
      <h4>관점별 매집 후보</h4>
      <div className="chips" role="group" aria-label="매집 후보 관점">
        {VIEW_KEYS.map((key) => (
          <button key={key} type="button" aria-pressed={key === selected}
            className={`chip chip-small${key === selected ? " chip-on" : ""}`} onClick={() => onSelect(key)}>
            {ACCUMULATION_VIEWS[key]}
          </button>
        ))}
      </div>
      {view == null ? <p className="muted">이 관점 자료를 가져오지 못했어요.</p>
        : view.error !== undefined ? <p className="muted">{view.error}</p>
        : <>
          <div className="accumulation-meta">
            <strong>{view.label} · {view.timeframe_ko}</strong>
            {view.reference_only && <span>{view.reference_label ?? "참고용"}</span>}
            {view.small_sample && stats && <span>표본 부족</span>}
          </div>
          {bands.length ? (
            <ul className="accumulation-bands">
              {bands.map((band, index) => (
                <li key={index}>
                  <strong>{bandLabel(band)} {price(band.low)}~{price(band.high)}</strong>
                  <span>{band.inside ? "구간 안" : "현재가 대비 " + signed(band.distance_pct) + "%"} · 점수 {band.score}</span>
                  {band.basis.length > 0 && <span className="muted">{band.basis.join(" · ")}</span>}
                </li>
              ))}
            </ul>
          ) : <p className="muted">가까운 후보 구간이 없어요.</p>}
          {stats && (
            <div className="accumulation-record">
              <strong>관점 합산 과거 검증</strong>
              <p>{stats.horizon_bars}봉 안 반등 {pct(stats.bounce_pct)} · 전체 {stats.n.toLocaleString("ko-KR")}건</p>
              <p>검증 구간 반등 {pct(stats.valid.bounce_pct)} · {stats.valid.n.toLocaleString("ko-KR")}건</p>
              <p>셔플 기준 {pct(stats.baseline_pct)}
                {stats.edge_pct != null && <> · 차이 {signed(stats.edge_pct, 1)}%p</>}
                {stats.edge_needed_pct != null && <> · 판정 기준 {stats.edge_needed_pct.toFixed(1)}%p</>}
              </p>
            </div>
          )}
          {legs?.items.length ? (
            <details className="accumulation-legs">
              <summary>상승·하락 다리 {legs.items.length}개</summary>
              {legs.sentence && <p>{legs.sentence}</p>}
              <ol>
                {legs.items.map((leg, index) => (
                  <li key={index}>
                    {leg.direction === "up" ? "상승" : "하락"} {signed(leg.pct)}%
                    {" · "}{leg.days == null ? `${leg.bars}봉` : `${leg.days}일`}
                    {leg.pct_per_day != null && ` · ${signed(leg.pct_per_day, 3)}%/일`}
                    {leg.live && " · 진행 중"}
                  </li>
                ))}
              </ol>
            </details>
          ) : null}
        </>}
      {accumulation?.note && <p className="muted">{accumulation.note}</p>}
    </section>
  );
}