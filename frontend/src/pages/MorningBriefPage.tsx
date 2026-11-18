import MorningBriefCard from "../components/MorningBriefCard";

/** #573 알림(#571 push url /#/briefs/morning?date=…)으로 연 아침 시황 — 그날 브리핑을 펼친 채로 */
export default function MorningBriefPage({ query }: { query: URLSearchParams }) {
  const date = query.get("date") ?? undefined;
  return (
    <div className="paper-page">
      <section className="card">
        <h2>아침 시황</h2>
        <MorningBriefCard key={date ?? "latest"} date={date && /^\d{4}-\d{2}-\d{2}$/.test(date) ? date : undefined} open />
      </section>
    </div>
  );
}
