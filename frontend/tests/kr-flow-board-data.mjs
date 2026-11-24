const dateMs = 86_400_000;
const sessions = [];
for (let stamp = Date.parse("2026-11-24T00:00:00Z"); sessions.length < 22; stamp -= dateMs) {
  const day = new Date(stamp);
  if (day.getUTCDay() > 0 && day.getUTCDay() < 6) sessions.unshift(day.toISOString().slice(0, 10));
}
const last = sessions.at(-1);
const rankDate = sessions.at(-2);
const holdingDate = sessions.at(-3);

function metadata(date, kind, unit, status = "available") {
  return { status, as_of: date, source: date ? `합성 ${kind} 원천` : null,
    published_at: date ? `${date}T08:20:00Z` : null, unit };
}

function ownership(date, ratio) {
  return { ...metadata(date, "지분율", "percent", ratio === null ? "no_data" : "available"), ratio_pct: ratio };
}

function observation(date, total, buy, sell, owned = null) {
  const complete = buy !== null && sell !== null;
  const sum = complete ? buy + sell : 0;
  return {
    date, turnover: { ...metadata(total === null ? null : date, "거래대금", total === null ? null : "KRW",
      total === null ? "no_data" : "available"), venue: total === null ? null : "krx", value_krw: total },
    foreign_flow: { ...metadata(complete ? date : null, "외국인 수급", "KRW", complete ? "available" : "source_unavailable"),
      unit: complete ? "KRW" : null, venue: complete ? "krx" : null,
      buy_krw: buy, sell_krw: sell, net_krw: complete ? buy - sell : null },
    ownership: ownership(owned === null ? null : date, owned),
    participation_pct: complete && total > 0 ? sum * 50 / total : null,
    participation_status: complete && total > 0 ? "available" : "missing_input",
    buy_sell_imbalance: sum ? (buy - sell) / sum : null,
    imbalance_status: !complete ? "missing_input" : sum ? "available" : "zero_denominator",
  };
}

const samsung = "005930.KS", sk = "000660.KS";
const koreaRanks = Array.from({ length: 50 }, (_, index) => {
  const ticker = index === 0 ? samsung : index === 1 ? sk : `${String(120000 + index)}.KS`;
  const name = index === 0 ? "삼성전자" : index === 1 ? "SK하이닉스" : `합성 종목 ${index + 1}`;
  const total = (100 - index) * 1_000_000_000;
  const missing = index === 49;
  return {
    ticker, name, rank: index + 1,
    latest: observation(rankDate, total, missing ? null : index === 0 ? 0 : (index + 2) * 120_000_000,
      missing ? null : (index + 2) * (index % 3 === 0 ? 300_000_000 : 120_000_000)),
    latest_ownership: ownership(holdingDate, index === 0 ? 47.5 : 0),
  };
});
function favorite(ticker, name, ratio, zeroLast = false) {
  return {
    ticker, name, latest_ownership: ownership(holdingDate, ratio),
    history: sessions.map((date, index) => {
      if (index === 5) return observation(date, null, null, null);
      const zero = zeroLast && date === last;
      return observation(date, 8_000_000_000, zero ? 0 : 1_000_000_000,
        zero ? 0 : index % 2 ? 1_000_000_000 : 500_000_000, date === holdingDate ? ratio : null);
    }),
  };
}

export const flowFixtures = {
  kospi: {
    market: "kospi", venue: "krx", status: "partial", latest_session: last, sessions,
    ranking: { ...metadata(rankDate, "순위", "KRW", "stale"), items: koreaRanks },
    favorites: { status: "partial", items: [favorite(samsung, "삼성전자", 47.5, true), favorite(sk, "SK하이닉스", 0)] },
    market_summary: {
      status: "stale", scope: "market",
      index: { ...metadata(last, "지수", "index_points"), value: 4869.96, change_pct: 0 },
      flow: { ...metadata(rankDate, "시장 수급", "KRW"), investors: { foreigner: { buy: 30, sell: 50, net: -20 } } },
    },
    sources: { turnover: { status: "available" }, foreign_flow: { status: "available" }, ownership: { status: "available" } },
  },
  kosdaq: {
    market: "kosdaq", venue: "krx", status: "partial", latest_session: last, sessions,
    ranking: { ...metadata(null, "순위", "KRW", "source_unavailable"), items: [] },
    favorites: { status: "partial", items: [favorite("247540.KQ", "에코프로비엠", 3.2)] },
    market_summary: { status: "source_unavailable", scope: "market", index: null, flow: null },
    sources: { turnover: { status: "source_unavailable" }, foreign_flow: { status: "source_unavailable" }, ownership: { status: "available" } },
  },
};
