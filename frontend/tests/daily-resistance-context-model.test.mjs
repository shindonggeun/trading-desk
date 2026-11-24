import assert from "node:assert/strict";
import test from "node:test";
import { canReadDailyResistanceNews, dailyResistanceNews, dailyResistanceNewsUnavailable } from "../src/dailyResistanceContext.ts";

const observed = "2026-11-24T22:31:00Z";
const article = (title, fields = {}) => ({
  title, kind: "news", source: "gdelt", time_basis: "gdelt_seen", relevance: "direct",
  at: "2026-11-24T22:28:00Z", publisher: "news.example", ...fields,
});

test("뉴스는 관찰 시각까지 발견된 같은 상품의 최신 직접 기사 세 건으로 제한한다", () => {
  const items = [
    article("이전 제목", { at: "2026-11-24T22:10:00Z" }),
    article("지난 기사", { at: "2026-11-24T22:15:00Z", stale: true }),
    article("관찰 시각 다음에 발견", { at: "2026-11-24T22:31:01Z" }),
    article("동일 시각에 발견", { at: "2026-11-25T07:31:00+09:00" }),
    article("그 전 뉴스"),
    article("구성종목 간접 뉴스", { relevance: "indirect" }),
    article("SEC 공시", { kind: "filing" }),
    article("발견 시각 없음", { at: null }),
    article("시간대 없음", { at: "2026-11-24T22:28:00" }),
    article("발견 시각 외 기준", { time_basis: "edgar_acceptance" }),
    article("원천은 구응답", { source: "yahoo" }),
  ];
  const actual = dailyResistanceNews("SOXL", observed, { ticker: "SOXL", items });
  assert.deepEqual(actual.map(({ title }) => title), ["동일 시각에 발견", "그 전 뉴스", "지난 기사"]);
  assert.equal(actual[2].stale, true);
});

test("응답 종목과 제목의 다른 상품을 제외하고 현물·ETF·선물 이름을 대체하지 않는다", () => {
  const titles = (ticker, items) => dailyResistanceNews(ticker, observed, { ticker, items }).map(({ title }) => title);
  assert.deepEqual(titles("SOXL", [
    article("SOXL ETF"),
    article("SOXL/USDT 연동 계약"), article("SOXLUSDT.P"), article("SOXL - USDT"),
    article("SOXL – USDT perpetual futures"), article("SOXL:USDT"), article("SOXL USDC perpetual futures"),
    article("SOXL와 SOXX ETF 비교"),
  ]), ["SOXL ETF"]);
  assert.deepEqual(titles("^NDX", [article("^NDX 관찰"), article("NQ=F 선물"), article("NAS100.CFD 가격")]), ["^NDX 관찰"]);
  assert.deepEqual(titles("NQ=F", [article("NQ=F 관찰"), article("^NDX 현물")]), ["NQ=F 관찰"]);
  assert.deepEqual(dailyResistanceNews("SOXX", observed, { ticker: "SOXL", items: [article("SOXX")] }), []);
  assert.deepEqual(dailyResistanceNews("NQ=F", observed, { ticker: "^NDX", items: [article("^NDX")] }), []);
  assert.equal(canReadDailyResistanceNews("SOXLUSDT", observed), false);
  assert.equal(canReadDailyResistanceNews("NAS100", observed), false);
});

test("구버전이나 누락 응답에는 근거를 추정하지 않는다", () => {
  assert.deepEqual(dailyResistanceNews("SOXL", observed, undefined), []);
  assert.deepEqual(dailyResistanceNews("SOXL", observed, { ticker: "SOXL" }), []);
  assert.deepEqual(dailyResistanceNews("SOXL", observed, { ticker: "SOXL", items: [null, {}] }), []);
  assert.equal(canReadDailyResistanceNews("SOXL", undefined), false);
  assert.equal(canReadDailyResistanceNews("SOXL", "2026-11-24"), false);
  assert.deepEqual(dailyResistanceNews("SOXL", "잘못된 시각", { ticker: "SOXL", items: [article("SOXL")] }), []);
});

test("원천 장애와 지연 상태는 같은 종목 뉴스가 없는 관찰과 구분한다", () => {
  const response = { ticker: "SOXL", items: [] };
  assert.equal(dailyResistanceNewsUnavailable("SOXL", { ...response, sources: [{ id: "gdelt", status: "unavailable" }] }), true);
  assert.equal(dailyResistanceNewsUnavailable("SOXL", { ...response, news: { status: "stale" } }), true);
  assert.equal(dailyResistanceNewsUnavailable("SOXL", { ...response, news: { status: "insufficient" } }), false);
  assert.equal(dailyResistanceNewsUnavailable("SOXX", { ...response, news: { status: "unavailable" } }), false);
  assert.equal(dailyResistanceNewsUnavailable("SOXL", { ticker: "SOXL", news: { status: "unavailable" } }), false);
});
