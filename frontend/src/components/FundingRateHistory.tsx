import { useEffect, useMemo, useRef, useState } from "react";
import { ColorType, HistogramSeries, LineSeries, createChart, type IChartApi } from "lightweight-charts";
import { fundingChartRows, fundingDayTime, fundingLineRuns, type FundingChartRow } from "../fundingRateChartModel";
import type { FundingDay, FundingSettlement } from "../fundingRateTypes";

const PERCENT = new Intl.NumberFormat("ko-KR", { style: "percent", signDisplay: "exceptZero", maximumFractionDigits: 8 });
const PRICE = new Intl.NumberFormat("ko-KR", { maximumFractionDigits: 8 });
const STATUS_LABEL: Record<FundingDay["status"], string> = {
  complete: "완료", missing: "결측", partial: "결제 일부 누락",
};

export function formatFundingRate(rate: number | null) {
  return rate === null ? "미제공" : PERCENT.format(rate);
}

export function formatMark(price: number | null) {
  return price === null ? "미제공" : PRICE.format(price);
}

function FundingPlot({ rows }: { rows: FundingChartRow[] }) {
  const host = useRef<HTMLDivElement>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    const el = host.current;
    if (!el) return;
    let chart: IChartApi | undefined;
    try {
      const style = getComputedStyle(el);
      const color = (name: string) => style.getPropertyValue(name).trim();
      const border = color("--border");
      const average = color("--funding-average");
      const mark = color("--funding-mark");
      const up = color("--up");
      const down = color("--down");
      chart = createChart(el, {
        autoSize: true,
        layout: { background: { type: ColorType.Solid, color: "transparent" }, textColor: color("--muted") },
        grid: { vertLines: { visible: false }, horzLines: { color: border } },
        leftPriceScale: { visible: true, borderColor: border },
        rightPriceScale: { visible: true, borderColor: border },
        timeScale: { borderColor: border, fixLeftEdge: true, fixRightEdge: true },
        localization: { locale: "ko-KR" },
      });
      const rateFormat = { type: "custom", minMove: 0.00000001, formatter: formatFundingRate } as const;
      const bars = chart.addSeries(HistogramSeries, {
        priceScaleId: "left", priceFormat: rateFormat, priceLineVisible: false, lastValueVisible: false,
      });
      bars.setData(rows.map(row => {
        const time = fundingDayTime(row.date);
        const rate = row.status === "complete" ? row.total_rate : null;
        return rate === null ? { time } : { time, value: rate, color: rate < 0 ? down : up };
      }));
      // 다른 계열의 기록이 있어도 0선은 항상 펀딩비 축에 놓는다.
      if (rows.some(row => row.status === "complete" && row.total_rate !== null)) {
        bars.createPriceLine({ price: 0, color: border, axisLabelVisible: false });
      }
      const zeroes = rows.filter(row => row.status === "complete" && row.total_rate === 0);
      if (zeroes.length) {
        chart.addSeries(LineSeries, {
          priceScaleId: "left", priceFormat: rateFormat, color: color("--muted"),
          lineVisible: false, pointMarkersVisible: true, pointMarkersRadius: 2,
          priceLineVisible: false, lastValueVisible: false,
        }).setData(zeroes.map(row => ({ time: fundingDayTime(row.date), value: 0 })));
      }

      // 값 없는 날짜를 사이에 두고 선을 연결하면 실제로 없던 값이 이어져 보인다.
      for (const run of fundingLineRuns(rows, row => row.status === "complete" ? row.average_7d : null)) {
        chart.addSeries(LineSeries, {
          priceScaleId: "left", color: average, lineWidth: 2, pointMarkersVisible: run.length === 1,
          priceFormat: rateFormat, priceLineVisible: false, lastValueVisible: false,
        }).setData(run);
      }
      for (const run of fundingLineRuns(rows, row => row.mark)) {
        chart.addSeries(LineSeries, {
          priceScaleId: "right", color: mark, lineWidth: 2, pointMarkersVisible: run.length === 1,
          priceFormat: { type: "custom", minMove: 0.00000001, formatter: formatMark },
          priceLineVisible: false, lastValueVisible: false,
        }).setData(run);
      }
      chart.timeScale().fitContent();
      setFailed(false);
    } catch {
      chart?.remove();
      setFailed(true);
      return;
    }
    return () => chart?.remove();
  }, [rows]);
  return (
    <>
      <div className="funding-plot" ref={host} role="img"
        aria-label="UTC 일별 펀딩비 막대, 7일 평균선, 계약 마크 가격선" hidden={failed} />
      {failed && <p role="status" className="muted small">차트를 그리지 못했습니다. 아래 표에서 수치를 확인하세요.</p>}
    </>
  );
}

export default function FundingRateHistory({ daily, settlements }: {
  daily: FundingDay[];
  settlements: FundingSettlement[];
}) {
  const rows = useMemo(() => fundingChartRows(daily, settlements), [daily, settlements]);
  const hasValues = rows.some(row => (row.status === "complete" && row.total_rate !== null) || row.mark !== null);
  const hasAverage = rows.some(row => row.status === "complete" && row.average_7d !== null);
  if (!rows.length) return null;
  return (
    <div className="funding-history">
      {hasValues ? (
        <>
          <div className="funding-axes small"><span>펀딩비 (%)</span><span>마크 가격 (USDT)</span></div>
          <FundingPlot rows={rows} />
          <ul className="funding-legend small" aria-label="차트 범례">
            <li><i className="funding-positive" /> + 양수</li>
            <li><i className="funding-negative" /> − 음수</li>
            <li><i className="funding-zero" /> 0 실제 0</li>
            <li><i className="funding-average" /> 일별 합계의 7일 평균</li>
            <li><i className="funding-mark" /> 일별 마지막 결제 마크 가격</li>
          </ul>
          {!hasAverage && <p className="muted small">7일 평균에 필요한 연속 유효 날짜가 부족합니다.</p>}
        </>
      ) : <p className="muted small">표시할 일별 수치가 없습니다.</p>}
      <details className="funding-values">
        <summary>일별 수치 보기</summary>
        <table aria-label="UTC 일별 펀딩비 이력">
          <thead><tr><th scope="col">UTC 날짜·상태</th><th scope="col">합계</th><th scope="col">7일 평균</th><th scope="col">마크 가격 (USDT)</th></tr></thead>
          <tbody>
            {rows.map(row => (
              <tr key={row.date}>
                <th scope="row"><time dateTime={row.date}>{row.date}</time><span className="muted">{STATUS_LABEL[row.status]}</span></th>
                <td>{formatFundingRate(row.total_rate)}</td>
                <td>{formatFundingRate(row.average_7d)}</td>
                <td>{formatMark(row.mark)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </details>
    </div>
  );
}
