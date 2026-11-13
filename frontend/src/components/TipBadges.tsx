import { useEffect, useState } from "react";
import { api } from "../api";
import type { Tip } from "../types";

/** #332 분석 화면: 이 종목에 지금 켜진 국면 팁(#330)을 배지로. 누르면 팁 페이지의 그 카드로 간다. */
export default function TipBadges({ ticker }: { ticker: string }) {
  const [tips, setTips] = useState<Tip[]>([]);
  useEffect(() => {
    let alive = true;
    api.tips(ticker).then((v) => alive && setTips(v.tips.filter((t) => t.active)), () => alive && setTips([]));
    return () => {
      alive = false;
    };
  }, [ticker]);
  if (!tips.length) return null;
  return (
    <div className="tip-badges" aria-label="해당 국면 팁">
      {tips.map((t) => (
        <a key={t.id} href={`#/tips?tip=${encodeURIComponent(t.id)}`} className={`tip-badge${t.weak ? " tip-badge-weak" : ""}`}
          title={`${t.condition}${t.weak ? " · 근거 약함" : ""}`}>
          ⚠ {t.title}{t.weak ? " (근거 약함)" : ""}
        </a>
      ))}
    </div>
  );
}
