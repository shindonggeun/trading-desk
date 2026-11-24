import { useId, useState } from "react";
import {
  NQ_CHANNEL_LABEL, NQ_PRICE_CONDITIONS, NQ_PRICE_LABEL, confirmedNqMarkers, nqChannelCopy, nqDraft, parseNqDraft,
  type NqOversoldDelivery, type NqOversoldPoint, type NqOversoldStudy, type NqWeeklyOversoldView,
} from "../nqWeeklyOversold";
import type { useNqWeeklyOversold } from "../useNqWeeklyOversold";
import "./NqWeeklyOversoldPanel.css";

type State = ReturnType<typeof useNqWeeklyOversold>;
const KST = new Intl.DateTimeFormat("ko-KR", {
  timeZone: "Asia/Seoul", year: "numeric", month: "2-digit", day: "2-digit",
  hour: "2-digit", minute: "2-digit", hourCycle: "h23",
});
const NUMBER = new Intl.NumberFormat("ko-KR", { maximumFractionDigits: 2 });
const STATUS = { matched: "확정 · 조건 충족", not_matched: "확정 · 조건 미충족", pending: "확정 대기", unavailable: "판정 불가" };
const HIT = { hit: "충족", miss: "미충족", unknown: "자료 부족" };
const DELIVERY: Record<NqOversoldDelivery["status"], string> = {
  pending: "대기", sending: "발송 중", sent: "발송 완료", unknown: "결과 미확인 · 자동 재발송 중지",
  failed: "실패", expired: "발송 기한 종료", superseded: "규칙 변경으로 종료", rate_limited: "요청 제한 · 재시도 대기",
  rate_limit_exhausted: "요청 제한 · 재시도 종료", unavailable: "채널 미확인", disabled: "채널 설정 꺼짐", needs_reconnect: "재연결 필요",
};
const REASON: Record<string, string> = {
  weekly_source_unavailable: "주봉 원천을 받지 못했어요.",
  unsupported_weekly_source: "이 원천의 주봉 확정 기준을 확인할 수 없어요.",
  invalid_weekly_timestamp: "주봉 조회 시각을 확인할 수 없어요.",
  invalid_weekly_bars: "봉의 날짜·연속성·가격을 확인할 수 없어요.",
  no_completed_week: "조회 범위에 마감한 주봉이 없어요.",
  stale_weekly_bars: "가장 최근 마감 주의 봉을 아직 받지 못했어요.",
  unconfirmed_weekly_close: "다음 주 CME 개장과 다음 주봉 라벨을 확인해야 해요.",
  weekly_evidence_error: "주봉 근거를 계산하지 못했어요.",
  insufficient_history: "계산에 필요한 확정 주봉이 부족해요.",
  atr_unavailable: "직전 주봉 ATR을 확인할 수 없어요.",
  channel_unavailable: "그 시점에 유효한 평행 채널이 없어요.",
  fib_anchors_unavailable: "피보나치에 필요한 고점·저점 기준이 부족해요.",
  invalid_price_level: "유효한 지지 가격대를 구하지 못했어요.",
  invalid_rsi: "유효한 RSI를 구하지 못했어요.",
  insufficient_overlap_evidence: "조건 겹침을 판단할 근거가 부족해요.",
  rsi_above_limit: "실제 RSI가 설정한 상한보다 높아요.",
  too_few_price_hits: "충족한 지지 조건 수가 설정 개수에 못 미쳐요.",
};

function numeric(value: number | null | undefined, suffix = ""): string {
  return typeof value === "number" && Number.isFinite(value) ? NUMBER.format(value) + suffix : "미확인";
}

function Stamp({ value }: { value: string | null }) {
  const epoch = value ? Date.parse(value) : NaN;
  return value && Number.isFinite(epoch) ? <time dateTime={value}>{KST.format(epoch)}</time> : <>미확인</>;
}

function Evidence({ point }: { point: NqOversoldPoint }) {
  const { evidence } = point;
  const firstPrice = NQ_PRICE_CONDITIONS.map(condition => evidence[condition]).find(row => row != null);
  const reason = point.reason ? REASON[point.reason] ?? (point.status === "unavailable" ? "받은 자료에서 판정 근거를 확인하지 못했어요." : undefined) : null;
  return (
    <>
      <p><strong>{STATUS[point.status]}</strong> · <time dateTime={point.week.start}>{point.week.start}</time> 주 (뉴욕)</p>
      {reason && <p className="muted small">{reason}</p>}
      {(evidence.rsi || firstPrice) && (
        <dl className="nq-oversold-grid">
          {evidence.rsi && <div><dt>실제 RSI(14) / 설정 상한</dt><dd>
            {numeric(evidence.rsi.value)} / {numeric(evidence.rsi.limit)} · {HIT[evidence.rsi.status]}
            {evidence.rsi.reason && <small>{REASON[evidence.rsi.reason] ?? "RSI 근거 부족"}</small>}
          </dd></div>}
          <div><dt>가격 조건 충족</dt><dd>{point.matched_conditions.length}개 / 최소 {point.min_hits}개</dd></div>
          {firstPrice && <>
            <div><dt>실제 주봉 저가</dt><dd>{numeric(firstPrice.actual_low, " 포인트")}</dd></div>
            <div><dt>실제 주봉 종가</dt><dd>{numeric(firstPrice.actual_close, " 포인트")}</dd></div>
          </>}
        </dl>
      )}
      {firstPrice && (
        <ul className="nq-oversold-list" aria-label="가격 조건 근거">
          {NQ_PRICE_CONDITIONS.map(condition => {
            const row = evidence[condition];
            if (!row) return null;
            return <li key={condition}>
              <strong>{NQ_PRICE_LABEL[condition]} · {HIT[row.status]}</strong>
              <span>{numeric(row.value, " 포인트")} · 허용 {row.tolerance != null ? "±" + numeric(row.tolerance, " 포인트") : "미확인"}</span>
              {row.state === "below" && <small>종가가 지지 허용 범위보다 낮아요.</small>}
              {row.reason && <small>{REASON[row.reason] ?? "가격 근거 미확인"}</small>}
              {row.anchor_weeks.length > 0 && <small>기준 주 {row.anchor_weeks.join(" · ")}</small>}
            </li>;
          })}
        </ul>
      )}
      <details className="nq-oversold-detail">
        <summary>주봉 기준과 원천</summary>
        <dl className="nq-oversold-grid">
          <div><dt>예정 마감 (한국)</dt><dd><Stamp value={point.week.closed_at} /></dd></div>
          <div><dt>확정 확인 기준 (한국)</dt><dd><Stamp value={point.week.confirmed_at} /></dd></div>
          <div><dt>원천</dt><dd>{point.week.source === "yahoo" ? "Yahoo Finance · NQ=F" : point.week.source ?? "미확인"}</dd></div>
          <div><dt>봉 수신 (한국)</dt><dd><Stamp value={point.week.fetched_at} /></dd></div>
        </dl>
        <p className="muted small">연속 선물의 롤오버는 미확인 상태예요. 개별 만기와 가격이 다를 수 있어요.</p>
      </details>
    </>
  );
}

function Study({ study }: { study: NqOversoldStudy }) {
  const c = study.coverage;
  return (
    <details className="nq-oversold-detail">
      <summary>4주·12주 관찰 자료</summary>
      <p className="muted small">
        {c.scanned_weeks}주 관찰 / 요청 {c.requested_weeks}주 · 판정 미확인 {c.unverified_weeks}주 · 충족 {c.matched_weeks}주
        {c.first_evaluated_week && c.last_week && <> · {c.first_evaluated_week}~{c.last_week}</>}
      </p>
      <p className="muted small">직전 주에서 유효 미충족→충족으로 바뀐 {c.edge_sample_count}건을 집계했어요. 현재 규칙의 재계산 결과예요.</p>
      {study.status === "available" && <dl className="nq-oversold-grid">
        {(["4w", "12w"] as const).map(key => {
          const horizon = study.horizons[key];
          return <div key={key}><dt>{horizon.weeks}주 후</dt><dd>
            완료 {horizon.observed_count}건 · 미확인 {horizon.unverified_count}건
            <small>종가 변화 중앙값 {numeric(horizon.median_return_pct, "%")}</small>
            <small>추가 하락 {study.severe_decline_threshold_pct}% 이상 {horizon.severe_additional_decline_count}건</small>
          </dd></div>;
        })}
      </dl>}
    </details>
  );
}

function RuleForm({ view, state }: { view: NqWeeklyOversoldView; state: State }) {
  const [draft, setDraft] = useState(() => nqDraft(view.settings));
  const [error, setError] = useState("");
  return (
    <form noValidate className="nq-oversold-form" onSubmit={event => {
      event.preventDefault();
      const parsed = parseNqDraft(draft);
      if (!parsed.settings) { setError(parsed.error); return; }
      setError("");
      void state.save(parsed.settings);
    }}>
      <fieldset disabled={state.loading || state.saving}>
        <legend>중요 알림 규칙</legend>
        <label className="nq-oversold-check"><input type="checkbox" checked={draft.enabled}
          onChange={event => setDraft({ ...draft, enabled: event.currentTarget.checked })} /> 중요 알림 사용</label>
        <div className="nq-oversold-grid">
          <label>RSI 상한
            <input type="number" min="0" max="100" step="any" inputMode="decimal" value={draft.rsiLimit}
              onChange={event => setDraft({ ...draft, rsiLimit: event.currentTarget.value })} />
          </label>
          <label>최소 충족 개수
            <input type="number" min="1" max={draft.priceConditions.length} step="1" inputMode="numeric" value={draft.minHits}
              onChange={event => setDraft({ ...draft, minHits: event.currentTarget.value })} />
          </label>
        </div>
        <fieldset>
          <legend>사용할 가격 조건</legend>
          {NQ_PRICE_CONDITIONS.map(condition => (
            <label key={condition} className="nq-oversold-check">
              <input type="checkbox" checked={draft.priceConditions.includes(condition)}
                onChange={event => setDraft({ ...draft, priceConditions: event.currentTarget.checked
                  ? [...draft.priceConditions, condition] : draft.priceConditions.filter(key => key !== condition) })} />
              {NQ_PRICE_LABEL[condition]}
            </label>
          ))}
        </fieldset>
        <button type="submit">{state.saving ? "저장 중…" : "알림 조건 저장"}</button>
      </fieldset>
      {error && <p className="error small" role="alert">{error}</p>}
    </form>
  );
}

function Channels({ view }: { view: NqWeeklyOversoldView }) {
  return (
    <>
      <h5>중요 알림 수신 채널</h5>
      <dl className="nq-oversold-grid">
        {view.channels.map(channel => <div key={channel.channel}>
          <dt>{NQ_CHANNEL_LABEL[channel.channel]}</dt><dd>{nqChannelCopy(channel)}</dd>
        </div>)}
      </dl>
      <a href="#/settings?notify">알림 채널 설정·시험 보내기</a>
      {view.deliveries.length > 0 && <details className="nq-oversold-detail">
        <summary>최근 발송 기록</summary>
        <ul className="nq-oversold-list">
          {view.deliveries.map(row => <li key={row.channel + row.week}>
            {row.week} 주 · {NQ_CHANNEL_LABEL[row.channel]} · {DELIVERY[row.status]} · 시도 {row.attempts}회
            <small>기록 <Stamp value={row.updated_at} /> (한국)</small>
            {row.retry_at && <small>재시도 기준 <Stamp value={row.retry_at} /> (한국)</small>}
          </li>)}
        </ul>
      </details>}
    </>
  );
}

export default function NqWeeklyOversoldPanel({ state, markersOn, onMarkers, selectedWeek, onSelectWeek, detailId }: {
  state: State; markersOn: boolean; onMarkers: (enabled: boolean) => void;
  selectedWeek: string | null; onSelectWeek: (week: string | null) => void; detailId: string;
}) {
  const titleId = useId();
  const [settingsOpen, setSettingsOpen] = useState(false);
  const { view, readFailed, loading, saveFailed, saved } = state;
  const markers = confirmedNqMarkers(view);
  const selected = markers.find(marker => marker.week.start === selectedWeek);
  return (
    <section className="nq-oversold-panel" aria-labelledby={titleId}>
      <h4 id={titleId}>주봉 과매도 겹침</h4>
      <p className="muted small">NQ=F · 나스닥100 E-mini 연속 선물 · 주봉</p>
      {saved && <p className="small" role="status">
        알림 조건을 저장했어요.{readFailed && " 새 관찰 결과를 불러오지 못했어요."}
      </p>}
      {saveFailed && <p className="error small" role="alert">알림 조건을 저장하지 못했어요. 설정과 권한을 확인한 뒤 다시 시도해 주세요.</p>}
      {readFailed && <div className="nq-oversold-actions">
        {!saved && <p className="muted small" role="status">주봉 관찰을 불러오지 못했어요.</p>}
        <button type="button" className="secondary" onClick={state.refresh}>주봉 관찰 다시 읽기</button>
      </div>}
      {!view && loading && <p className="muted small" role="status">주봉 관찰 확인 중…</p>}
      {view && <>
        {view.current.forming_week && <p className="small">
          <strong>진행 주</strong> · <time dateTime={view.current.forming_week.start}>{view.current.forming_week.start}</time>
          {view.current.forming_week.source_present ? " · 원천 봉 수신, 확정 대기" : " · 원천 봉 대기"}
        </p>}
        <Evidence point={view.current} />
        <p className="muted small">관찰 시각 <Stamp value={view.current.observed_at} /> (한국)</p>
        <div className="nq-oversold-actions">
          <label className="nq-oversold-check"><input type="checkbox" checked={markersOn}
            onChange={event => onMarkers(event.currentTarget.checked)} /> 차트에 과매도 마커 표시</label>
          <button type="button" className="secondary" onClick={state.refresh} disabled={loading || state.saving}>새로 읽기</button>
        </div>
        <p className="muted small">과거 표식은 현재 규칙으로 확정 주봉을 재계산한 관찰이에요. 당시 발송은 최근 발송 기록에서 확인해요.</p>
        {markers.length > 0 ? (
          <div className="nq-oversold-actions" role="group" aria-label="최근 과매도 충족 주">
            {markers.slice(-5).reverse().map(marker => (
              <button type="button" className="chip" aria-pressed={selectedWeek === marker.week.start} key={marker.week.start}
                onClick={() => onSelectWeek(selectedWeek === marker.week.start ? null : marker.week.start)}>
                {marker.week.start} 주
              </button>
            ))}
          </div>
        ) : <p className="muted small">관찰 범위에 표시할 확정 충족 주가 없어요.</p>}
        {selected && <div id={detailId} className="nq-oversold-selection">
          <h5>{selected.week.start} 주 관찰 근거</h5>
          <Evidence point={selected} />
        </div>}
        <Study study={view.study} />
        <details className="nq-oversold-detail" open={settingsOpen} onToggle={event => setSettingsOpen(event.currentTarget.open)}>
          <summary>과매도 알림 설정</summary>
          <p className="muted small">RSI {numeric(view.settings.rsi_limit)} 이하 · 가격 조건 {view.settings.min_hits}개 이상 · 중요 알림 {view.settings.enabled ? "켜짐" : "꺼짐"}</p>
          <p className="muted small">같은 규칙의 인접한 확정 주가 미충족에서 충족으로 바뀌고 24시간 안에 확인될 때 알림을 판단해요. 첫 관찰과 규칙 변경 직후에는 비교 기준을 잡아요.</p>
          {view.can_edit ? <>
            <RuleForm key={view.revision} view={view} state={state} />
            <Channels view={view} />
          </> : <p className="muted small">데스크 소유자가 알림 조건과 수신 채널을 관리해요.</p>}
        </details>
      </>}
    </section>
  );
}
