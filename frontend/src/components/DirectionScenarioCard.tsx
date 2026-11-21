import { historyText, SCENARIO_STATUS, scenarioRegime, SIDE_KO, type DirectionScenario, type ScenarioSide } from "../directionScenarios";
import { price } from "../format";

export default function DirectionScenarioCard({ side, scenario, selected, onSelect }: {
  side: ScenarioSide;
  scenario: DirectionScenario | undefined;
  selected: boolean;
  onSelect: () => void;
}) {
  const label = SIDE_KO[side];
  if (!scenario) return <div className="hz-scenario"><strong>{label}</strong><p className="muted small">시나리오 정보 없음</p></div>;
  const { history, reason, status, confirmation, basis } = scenario;
  const regime = scenarioRegime(scenario.regime);
  const priced = status !== "no_setup";
  return (
    <div className="hz-scenario">
      <button type="button" className={`chip${selected ? " chip-on" : ""}`} aria-pressed={selected}
        aria-label={`${label} 시나리오 선택`} onClick={onSelect}>{label}</button>
      <p><strong>{SCENARIO_STATUS[status]}</strong>{regime && ` · ${regime}`}</p>
      {reason && <p>{reason}</p>}
      {priced && (
        <dl className="hz-scenario-prices">
          {scenario.entry_price != null && <><dt>진입</dt><dd>{price(scenario.entry_price)}</dd></>}
          {scenario.entry_zone && <><dt>진입 구간</dt><dd>{scenario.entry_zone.map(price).join("~")}</dd></>}
          {scenario.stop_loss != null && <><dt>손절</dt><dd>{price(scenario.stop_loss)}</dd></>}
          {scenario.targets.map((target, i) => (
            <div key={i}><dt>목표 {scenario.targets.length > 1 ? i + 1 : ""}</dt>
              <dd>{price(target.price)}{target.rr != null && ` · ${target.rr.toFixed(1)}R`}</dd></div>
          ))}
        </dl>
      )}
      {priced && (confirmation.trigger_text || confirmation.trigger_price != null) && (
        <p>{confirmation.trigger_price != null && `확인 가격 ${price(confirmation.trigger_price)} `}{confirmation.trigger_text}</p>
      )}
      {basis.length > 0 && <p className="muted">{basis.join(" · ")}</p>}
      <p>{historyText(history)}</p>
      {history.start && history.end && <p className="muted">{history.start} ~ {history.end}</p>}
      {history.win_rule && <p className="muted">{history.win_rule}</p>}
    </div>
  );
}
