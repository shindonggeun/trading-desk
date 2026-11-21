import type { AccumulationBand, AccumulationView, ChartReadFrame } from "./types";

const shownPrice = (value: number) => value.toLocaleString(undefined, { maximumFractionDigits: value >= 1000 ? 0 : 2 });
const range = (band: AccumulationBand) => `${shownPrice(band.low)}~${shownPrice(band.high)}`;
const valid = (value: number | null | undefined): value is number => value != null && Number.isFinite(value) && value > 0;

/** 선택 관점의 근거 있는 구간만 사용한다. 서버가 오래된 응답을 주면 가격을 추측하지 않는다. */
export function beginnerGuide(frame: ChartReadFrame, view: AccumulationView | null | undefined) {
  const current = valid(frame.price) ? frame.price : null;
  const bands = view && view.error === undefined ? [...view.below, ...view.above] : [];
  const supply = current == null ? null : bands
    .filter((band) => band.role === "supply" && valid(band.low) && valid(band.high) && band.low <= band.high && band.high >= current)
    .sort((a, b) => Math.max(a.low - current, 0) - Math.max(b.low - current, 0))[0] ?? null;
  const support = current == null ? null : bands
    .filter((band) => band.role === "accumulation" && valid(band.low) && valid(band.high) && band.low <= band.high && band.low <= current)
    .sort((a, b) => Math.max(current - a.high, 0) - Math.max(current - b.high, 0))[0] ?? null;

  const now = current == null ? "현재 가격을 확인할 수 없어요." : `지금 가격은 ${shownPrice(current)}예요.`;
  const supplyReason = supply?.basis.some((reason) => reason.includes("매물대"))
    ? "예전에 거래가 많아 올라갈 때 매도가 나올 수 있는 구간이에요."
    : "가격이 올라갈 때 상승세가 막힐 수 있는 구간이에요.";
  const resistance = supply && current != null
    ? `위에서 조심할 곳은 ${range(supply)}이에요. ${current >= supply.low
      ? "이미 이 구간에 들어왔어요."
      : `구간 시작까지 약 ${((supply.low / current - 1) * 100).toFixed(1)}% 남았어요.`} ${supplyReason}`
    : "위에서 조심할 뚜렷한 가격 구간은 아직 없어요.";
  const holder = supply
    ? `${range(supply)}에서 상승이 막히는지 보고 이익을 일부 정리할지 살펴볼 수 있어요.`
    : "위에서 막히는 가격이 확인될 때까지 움직임을 살펴보세요.";
  const entry = current == null ? "지금은 지켜볼 만한 진입 가격이 뚜렷하지 않아요."
    : support
      ? `${range(support)}에서 하락이 멈추고 ${shownPrice(support.high)} 위로 다시 올라서는지 확인해 보세요. ${shownPrice(support.low)} 아래로 다시 내려가면 진입 판단을 미뤄요.`
      : supply
        ? `${shownPrice(supply.high)} 위로 올라선 뒤 다시 그 가격을 지키는지 확인해 보세요. ${shownPrice(supply.high)} 아래로 다시 내려가면 진입 판단을 미뤄요.`
        : "지금은 지켜볼 만한 진입 가격이 뚜렷하지 않아요.";
  return { now, resistance, holder, entry };
}
