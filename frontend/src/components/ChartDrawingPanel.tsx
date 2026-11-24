import { PriceScaleMode, type IChartApi, type ISeriesApi, type MouseEventParams, type Time } from "lightweight-charts";
import { useCallback, useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from "react";
import {
  MAX_DRAWINGS, anchorCount, formatDrawingAnchors, measurePrice, readDrawings, validateAnchors, writeDrawings,
  type ChartDrawing, type DrawingAnchor, type DrawingKind,
} from "../chartDrawingModel";
import { ChartDrawingPrimitive } from "./chartDrawingPrimitive";
import { findDrawingPractice, type DrawingCandle, type DrawingPractice, type PracticeKind } from "./chartDrawingPractice";
import "./ChartDrawingPanel.css";

export type DrawingChart = {
  chart: IChartApi;
  series: ISeriesApi<"Candlestick">;
  alive: { current: boolean };
};
export type DrawingQuote = { price: number; label: string; observedAt?: string | null };

const LABEL: Record<DrawingKind, string> = {
  horizontal: "수평선", trend: "두 점 추세선", channel: "평행 채널", measure: "가격 거리",
};
const PRACTICE: Record<PracticeKind, string> = {
  horizontal: "수평선 연습", rising: "상승선 연습", falling: "하락선 연습", channel: "채널 연습",
};
const NUM = new Intl.NumberFormat("ko-KR", { maximumFractionDigits: 8 });
const DELTA = new Intl.NumberFormat("ko-KR", { maximumFractionDigits: 8, signDisplay: "exceptZero" });
const PERCENT = new Intl.NumberFormat("ko-KR", { maximumFractionDigits: 2, signDisplay: "exceptZero" });
const SMALL = new Intl.NumberFormat("ko-KR", { maximumSignificantDigits: 4, signDisplay: "always" });
const POINTS = ["A", "B", "C"];

function signed(value: number, formatter: Intl.NumberFormat, threshold: number): string {
  if (value === 0 || Math.abs(value) >= threshold) return formatter.format(value);
  if (Math.abs(value) >= 1e-8) return SMALL.format(value);
  return (value > 0 ? "+" : "") + value.toExponential(2);
}

type Placement = {
  kind: DrawingKind;
  anchors: DrawingAnchor[];
  practice?: DrawingPractice;
  replace?: { index: number; drawing: ChartDrawing };
  quote?: DrawingQuote;
  direction?: -1 | 1;
};

type Props = {
  ticker: string;
  interval: string;
  currency: string;
  drawingChart: DrawingChart | null;
  candles: DrawingCandle[];
  currentQuote: () => DrawingQuote | null;
  focusChart: () => void;
  onPlacement: (active: boolean) => void;
};

function keyFor(time: Time | undefined) {
  return typeof time === "object"
    ? [time.year, String(time.month).padStart(2, "0"), String(time.day).padStart(2, "0")].join("-") : time;
}

export default function ChartDrawingPanel({
  ticker, interval, currency, drawingChart, candles, currentQuote, focusChart, onPlacement,
}: Props) {
  const id = useId();
  const panel = useRef<HTMLElement>(null);
  const [primitive] = useState(() => new ChartDrawingPrimitive());
  const [open, setOpen] = useState(false);
  const [drawings, setDrawings] = useState(() => readDrawings(ticker, interval));
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [placement, setPlacement] = useState<Placement | null>(null);
  const [review, setReview] = useState<DrawingPractice | null>(null);
  const [notice, setNotice] = useState("");
  const [storageFailed, setStorageFailed] = useState(false);
  const [copyText, setCopyText] = useState("");
  const [keyboardTime, setKeyboardTime] = useState("");
  const [keyboardPrice, setKeyboardPrice] = useState("");
  const byTime = useMemo(() => new Map(candles.map(candle => [String(candle.time), candle])), [candles]);

  const selected = drawings.find(drawing => drawing.id === selectedId);
  const guide = placement?.practice?.steps[placement.anchors.length];
  const last = candles.at(-1);
  const active = placement !== null;
  const keyboardOptions = useMemo(() => candles.slice().reverse(), [candles]);

  useLayoutEffect(() => {
    onPlacement(active);
    return () => onPlacement(false);
  }, [active, onPlacement]);

  useEffect(() => {
    if (!drawingChart?.alive.current) return;
    const { series, alive } = drawingChart;
    series.attachPrimitive(primitive);
    return () => { if (alive.current) series.detachPrimitive(primitive); };
  }, [drawingChart, primitive]);

  useEffect(() => {
    primitive.setDrawings(drawings, open ? selectedId : null,
      placement && !placement.replace ? placement : undefined, guide?.anchor ?? review?.reviewAnchor);
  }, [drawings, selectedId, open, placement, guide, review, primitive]);

  const resetPlacement = useCallback(() => {
    setPlacement(null);
    setNotice("점 선택을 취소했습니다.");
  }, []);
  useEffect(() => {
    if (!active) return;
    const onEscape = (event: KeyboardEvent) => {
      const section = panel.current?.closest(".price-chart");
      if (event.key !== "Escape" || event.defaultPrevented || !(event.target instanceof Element) || !section?.contains(event.target)) return;
      event.preventDefault();
      event.stopPropagation();
      resetPlacement();
      panel.current?.querySelector<HTMLButtonElement>(".chart-drawing-toggle")?.focus({ preventScroll: true });
    };
    document.addEventListener("keydown", onEscape, true);
    return () => document.removeEventListener("keydown", onEscape, true);
  }, [active, resetPlacement]);

  function persist(next: ChartDrawing[]) {
    setDrawings(next);
    setStorageFailed(!writeDrawings(ticker, interval, next));
  }

  const accept = (anchor: DrawingAnchor) => {
    if (!placement || !Number.isFinite(anchor.price) || anchor.price <= 0) return;
    const { kind, anchors, practice, replace, direction } = placement;
    const next = replace ? replace.drawing.anchors.map((value, index) => index === replace.index ? anchor : value)
      : [...anchors, anchor];
    if (!replace && next.length < anchorCount(kind)) {
      if ((kind === "trend" || kind === "channel") && next.length === 2 && next[0].time === next[1].time) {
        setNotice("기준선의 두 번째 점은 다른 봉에서 선택하세요.");
        return;
      }
      setPlacement({ ...placement, anchors: next });
      setNotice("");
      return;
    }
    if (direction && (anchor.price - anchors[0].price) * direction <= 0) {
      setNotice(direction < 0 ? "기준 가격보다 아래의 지지를 선택하세요." : "기준 가격보다 위의 저항을 선택하세요.");
      return;
    }
    if (!validateAnchors(kind, next) || (kind === "channel" && !primitive.canDrawChannel(next))) {
      setNotice("기준선의 두 점은 서로 다른 봉에, 채널의 세 번째 점은 기준선 밖에 있어야 합니다.");
      return;
    }
    const drawingId = replace?.drawing.id ?? [...crypto.getRandomValues(new Uint32Array(4))].map(value => value.toString(16)).join("-");
    const drawing: ChartDrawing = { id: drawingId, kind, anchors: next };
    persist(replace ? drawings.map(entry => entry.id === drawingId ? drawing : entry) : [...drawings, drawing]);
    setSelectedId(drawingId);
    setPlacement(null);
    setReview(practice ?? null);
    setNotice(replace ? "선택한 점을 수정했습니다." : "저장했습니다 · " + LABEL[kind]);
  };

  const acceptRef = useRef(accept);
  acceptRef.current = accept;
  useEffect(() => {
    if (!active || !drawingChart?.alive.current) return;
    const { chart, series, alive } = drawingChart;
    const click = (param: MouseEventParams<Time>) => {
      if (!param.point || (param.paneIndex ?? 0) !== 0) return;
      const time = keyFor(param.time);
      const candle = time == null ? undefined : byTime.get(String(time));
      if (!candle) {
        setNotice("시세가 표시된 봉에서 점을 선택하세요.");
        return;
      }
      if (guide) {
        const targetY = series.priceToCoordinate(guide.anchor.price);
        if (candle.time !== guide.anchor.time || targetY == null || Math.abs(targetY - param.point.y) > 20) {
          setNotice("표시된 원에 맞춰 봉의 꼬리를 선택하세요.");
          return;
        }
        acceptRef.current(guide.anchor);
      } else {
        const price = series.coordinateToPrice(param.point.y);
        if (price == null || !Number.isFinite(price) || price <= 0) return;
        acceptRef.current({ time: candle.time, stamp: candle.stamp, price: Number(price.toPrecision(10)) });
      }
      focusChart();
    };
    chart.subscribeClick(click);
    return () => { if (alive.current) chart.unsubscribeClick(click); };
  }, [active, drawingChart, byTime, guide, focusChart]);

  function start(kind: DrawingKind, extra?: Pick<Placement, "anchors" | "quote" | "direction" | "practice">) {
    if (drawings.length >= MAX_DRAWINGS) {
      setNotice("저장 가능한 작도 수에 도달했습니다. 기존 작도를 삭제한 뒤 추가하세요.");
      return;
    }
    setPlacement({ kind, anchors: [], ...extra });
    setReview(null);
    setCopyText("");
    setNotice("");
    setKeyboardTime("");
    setKeyboardPrice("");
  }

  function startPractice(requested: PracticeKind) {
    if (!drawingChart) return;
    const practice = findDrawingPractice(candles, requested,
      drawingChart.series.priceScale().options().mode === PriceScaleMode.Logarithmic);
    if (!practice) {
      setNotice("최근 확정 봉에서 이 연습에 필요한 반응점을 찾지 못했습니다. 다른 유형이나 시간봉에서 시도하세요.");
      return;
    }
    const scale = drawingChart.chart.timeScale();
    const indices = [...practice.steps.map(step => step.anchor), practice.reviewAnchor]
      .filter((point): point is DrawingAnchor => point != null)
      .map(point => scale.timeToIndex(point.time as Time))
      .filter((index): index is NonNullable<typeof index> => index != null);
    const range = scale.getVisibleLogicalRange();
    if (range && indices.some(index => index < range.from + 1 || index > range.to - 1)) {
      const low = Math.min(...indices);
      const high = Math.max(...indices);
      scale.setVisibleLogicalRange({ from: low - 4, to: Math.max(high + 4, low + 12) });
    }
    start(practice.kind, { anchors: [], practice });
  }

  function startCurrent(direction: -1 | 1) {
    const quote = currentQuote();
    if (!quote || !last || quote.price <= 0 || !Number.isFinite(quote.price)) return;
    start("measure", { anchors: [{ time: last.time, stamp: last.stamp, price: quote.price }], quote, direction });
  }

  function selectKeyboard() {
    const candle = byTime.get(keyboardTime) ?? last;
    const price = Number(keyboardPrice);
    if (!candle || !Number.isFinite(price) || price <= 0) {
      setNotice("봉을 고르고 0보다 큰 가격을 입력하세요.");
      return;
    }
    accept({ time: candle.time, stamp: candle.stamp, price });
    setKeyboardPrice("");
  }

  const measurement = selected?.kind === "measure" ? measurePrice(selected.anchors[0].price, selected.anchors[1].price) : null;
  const nextPoint = placement && POINTS[placement.replace?.index ?? placement.anchors.length];

  return (
    <section ref={panel} className="chart-drawing">
      <button type="button" className="chip chart-drawing-toggle" aria-expanded={open} aria-controls={id} onClick={() => {
        setOpen(!open);
        if (open) { setPlacement(null); setReview(null); setNotice(""); setCopyText(""); }
      }}>수동 작도</button>
      {open && (
        <div id={id} className="chart-drawing-panel">
          <div className="chart-drawing-buttons" role="group" aria-label="수동 작도 도구">
            {(Object.keys(LABEL) as DrawingKind[]).map(kind => (
              <button key={kind} type="button" className="secondary" aria-pressed={placement?.kind === kind && !placement.practice && !placement.direction && !placement.replace}
                disabled={!last} onClick={() => start(kind)}>{LABEL[kind]}</button>
            ))}
            <button type="button" className="secondary" disabled={!last} onClick={() => startCurrent(-1)}>현재가 → 지지</button>
            <button type="button" className="secondary" disabled={!last} onClick={() => startCurrent(1)}>현재가 → 저항</button>
          </div>
          <details className="chart-drawing-help">
            <summary>실제 봉으로 따라 하기</summary>
            <p>연습은 표시된 꼬리를 순서대로 눌러 진행합니다. 몸통을 기준으로 바꾸려면 저장 후 기준점 모두를 몸통 쪽으로 옮기세요.</p>
            <div className="chart-drawing-buttons">
              {(Object.keys(PRACTICE) as PracticeKind[]).map(kind => (
                <button key={kind} type="button" className="secondary" disabled={!last} onClick={() => startPractice(kind)}>{PRACTICE[kind]}</button>
              ))}
            </div>
          </details>
          {placement && (
            <div className="chart-drawing-placement">
              <p role="status">
                {guide ? (placement.anchors.length + 1) + "/" + placement.practice!.steps.length + " · " + guide.instruction
                  : nextPoint + "점 · 가격 차트의 봉을 누르세요."}
                {guide && <span className="chart-drawing-guide-value"><time dateTime={guide.anchor.stamp}>{guide.anchor.stamp}</time> · {NUM.format(guide.anchor.price)} {currency}</span>}
                {placement.quote && <span className="chart-drawing-guide-value">A 기준 {NUM.format(placement.quote.price)} {currency} · {placement.quote.label}
                  {placement.quote.observedAt && <> · <time dateTime={placement.quote.observedAt}>{placement.quote.observedAt}</time></>}</span>}
              </p>
              <div className="chart-drawing-buttons">
                {guide && <button type="button" className="secondary" onClick={() => accept(guide.anchor)}>안내 좌표 선택</button>}
                <button type="button" className="secondary" onClick={resetPlacement}>작도 취소</button>
              </div>
              {!guide && <details>
                <summary>키보드로 점 지정</summary>
                <div className="chart-drawing-fields">
                  <label>기준 봉
                    <select value={byTime.has(keyboardTime) ? keyboardTime : String(last?.time ?? "")} onChange={event => setKeyboardTime(event.currentTarget.value)}>
                      {keyboardOptions.map(candle => <option key={candle.time} value={String(candle.time)}>{candle.stamp}</option>)}
                    </select>
                  </label>
                  <label>기준 가격 ({currency})
                    <input type="number" min="0" step="any" inputMode="decimal" value={keyboardPrice}
                      onChange={event => setKeyboardPrice(event.currentTarget.value)} onKeyDown={event => {
                        if (event.key === "Enter") { event.preventDefault(); selectKeyboard(); }
                      }} />
                  </label>
                  <button type="button" className="secondary" onClick={selectKeyboard}>{nextPoint}점 지정</button>
                </div>
              </details>}
            </div>
          )}
          {review && <p role="status" className="chart-drawing-review">{review.review}</p>}
          {notice && <p className="small" role="status">{notice}</p>}
          {storageFailed && <p className="small error" role="alert">브라우저 저장에 실패했습니다. 이 화면을 닫으면 변경을 잃을 수 있습니다.</p>}
          {drawings.length > 0 && (
            <details open={selected != null} className="chart-drawing-saved">
              <summary>저장된 작도 {drawings.length}개</summary>
              <label className="chart-drawing-selection">수동 작도 선택
                <select value={selectedId ?? ""} onChange={event => {
                  setSelectedId(event.currentTarget.value || null); setPlacement(null); setReview(null); setCopyText(""); setNotice("");
                }}>
                  <option value="">작도 선택</option>
                  {drawings.map((drawing, index) => (
                    <option value={drawing.id} key={drawing.id}>{index + 1} · {LABEL[drawing.kind]} · {NUM.format(drawing.anchors[0].price)} {currency}</option>
                  ))}
                </select>
              </label>
              {selected && <>
                {measurement && <p className="chart-drawing-result" role="status">
                  A {NUM.format(selected.anchors[0].price)} → B {NUM.format(selected.anchors[1].price)} {currency}
                  {" · "}{signed(measurement.difference, DELTA, 1e-8)} {currency} ({signed(measurement.percent, PERCENT, .01)}%)
                </p>}
                <ul className="chart-drawing-anchors">
                  {selected.anchors.map((anchor, index) => (
                    <li key={index}>
                      <span>{POINTS[index]} · <time dateTime={anchor.stamp}>{anchor.stamp}</time> · {NUM.format(anchor.price)} {currency}</span>
                      <button type="button" className="secondary" onClick={() => {
                        setPlacement({ kind: selected.kind, anchors: [], replace: { index, drawing: selected } });
                        setKeyboardTime(String(anchor.time)); setKeyboardPrice(String(anchor.price)); setReview(null); setNotice(""); setCopyText("");
                      }}>{POINTS[index]}점 이동</button>
                    </li>
                  ))}
                </ul>
                <div className="chart-drawing-buttons">
                  <button type="button" className="secondary" onClick={async () => {
                    const text = formatDrawingAnchors(selected, ticker, interval, currency);
                    try { await navigator.clipboard.writeText(text); setNotice("기준봉 시각과 가격을 복사했습니다."); setCopyText(""); }
                    catch { setCopyText(text); }
                  }}>앵커 복사</button>
                  <button type="button" className="secondary" onClick={() => {
                    persist(drawings.filter(entry => entry.id !== selected.id));
                    setSelectedId(null); setPlacement(null); setReview(null); setCopyText(""); setNotice("작도를 삭제했습니다.");
                  }}>작도 삭제</button>
                </div>
                {copyText && <label className="chart-drawing-selection">복사용 앵커<textarea readOnly value={copyText} onFocus={event => event.currentTarget.select()} /></label>}
              </>}
            </details>
          )}
        </div>
      )}
    </section>
  );
}
