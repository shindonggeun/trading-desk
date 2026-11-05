import { AGENTS, ANALYST_CHOICES, TEAM_LABEL, type Team } from "../agents";
import type { DeskEvent, RunStatus } from "../types";

type State = "done" | "speaking" | "waiting" | "skipped";

/** Who has spoken, who spoke last, and who is still to come. */
export default function AgentRail({ events, status, analysts }: { events: DeskEvent[]; status: RunStatus; analysts: string[] }) {
  const spoken = new Set(events.filter((e) => e.type === "message").map((e) => e.agent));
  const last = [...events].reverse().find((e) => e.type === "message")?.agent;
  const skipped = new Set(ANALYST_CHOICES.filter((c) => !analysts.includes(c.key)).map((c) => c.agent));
  const anyBeyondAnalysts = AGENTS.some((a) => a.team !== "analyst" && spoken.has(a.id));

  const stateOf = (id: string, team: Team): State => {
    if (skipped.has(id)) return "skipped";
    if (status === "running") {
      // Analysts all work at once, so each is "speaking" until its report lands.
      if (team === "analyst" && !spoken.has(id) && !anyBeyondAnalysts) return "speaking";
      if (id === last) return "speaking";
    }
    return spoken.has(id) ? "done" : "waiting";
  };

  const teams = [...new Set(AGENTS.map((a) => a.team))];
  return (
    <aside className="rail card">
      {teams.map((team) => (
        <div key={team} className="rail-team">
          <div className="rail-team-label">{TEAM_LABEL[team]}</div>
          {AGENTS.filter((a) => a.team === team && (!a.onlyIfSpoken || spoken.has(a.id))).map((a) => {
            const s = stateOf(a.id, a.team);
            return (
              <div key={a.id} className={`rail-agent rail-${s}`}>
                <span className="dot" />
                {a.name}
              </div>
            );
          })}
        </div>
      ))}
    </aside>
  );
}
