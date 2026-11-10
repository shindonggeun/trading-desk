import { time } from "../format";
import type { NewsImpact, NewsRead, NewsSentiment } from "../types";

/**
 * #245 헤드라인 판정(#242 desk/news_digest) 표시 조각 — 거시 브리핑 · 섹터 이슈가 같이 쓴다.
 * 판정이 없는 헤드라인(모델 실패 · 아직 안 읽음)은 원문 그대로 보인다.
 */

// 외부 기사 주소는 http(s) 만 링크로 — 그 밖의 주소(javascript: 등)는 제목 글자로만 둔다
const SAFE_URL = /^https?:\/\//i;
const IMPACT_KO: Record<NewsImpact, string> = { high: "영향 상", mid: "영향 중", low: "영향 하" };

/** 긍정 · 중립 · 부정 % 한 막대 */
export function Lean({ s }: { s: NewsSentiment }) {
  const label = `긍정 ${s.pos}% · 부정 ${s.neg}%`;
  return (
    <span className="nr-lean" role="img" aria-label={label} title={`${label} · 중립 ${s.neu}%`}>
      <span className="nr-lean-track">
        <span className="nr-pos" style={{ width: `${s.pos}%` }} />
        <span className="nr-neu" style={{ width: `${s.neu}%` }} />
        <span className="nr-neg" style={{ width: `${s.neg}%` }} />
      </span>
      <span className="small num"><span className="up">{s.pos}</span>/<span className="down">{s.neg}</span>%</span>
    </span>
  );
}

export function Impact({ v }: { v: NewsImpact }) {
  return <span className={`nr-impact nr-impact-${v}`}>{IMPACT_KO[v]}</span>;
}

type Head = { title: string; url: string; domain: string | null; seen: string | null } & Partial<NewsRead>;

/** 한글 제목을 크게(원문 링크), 원문 제목은 작게, 긍정/부정 · 영향 · 왜 그런지 한 줄 */
export function NewsHeadline({ h }: { h: Head }) {
  const main = h.title_ko || h.title;
  return (
    <div className="nr-head">
      <div>
        {SAFE_URL.test(h.url) ? (
          <a href={h.url} target="_blank" rel="noopener noreferrer" className="link">{main}</a>
        ) : (
          <span>{main}</span>
        )}
        <span className="muted small"> {[h.domain, h.seen && time(h.seen)].filter(Boolean).join(" · ")}</span>
      </div>
      {h.title_ko && <div className="nr-orig muted small" lang="en">{h.title}</div>}
      {(h.sentiment || h.impact) && (
        <div className="nr-read small">
          {h.sentiment && <Lean s={h.sentiment} />}
          {h.impact && <Impact v={h.impact} />}
          {h.assets && h.assets.length > 0 && <span className="muted">{h.assets.join(" · ")}</span>}
        </div>
      )}
      {h.why_ko && <div className="nr-why small">{h.why_ko}</div>}
    </div>
  );
}
