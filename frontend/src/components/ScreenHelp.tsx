import { useCallback, useEffect, useId, useRef, useState } from "react";
import { SCREEN_HELP, type ScreenHelpId } from "../screenHelp";
import "./ScreenHelp.css";

function ScreenHelpCard({ id }: { id: ScreenHelpId }) {
  const { title, summary, details } = SCREEN_HELP[id];
  const panel = useId();
  const control = useRef<HTMLDivElement>(null);
  const [hovered, setHovered] = useState(false);
  const [focused, setFocused] = useState(false);
  const [pinned, setPinned] = useState(false);
  const open = hovered || focused || pinned;

  const dismiss = useCallback(() => {
    setHovered(false);
    setFocused(false);
    setPinned(false);
  }, []);

  useEffect(() => {
    if (!open) return;
    const onEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") dismiss();
    };
    const onOutside = (event: PointerEvent) => {
      if (event.target instanceof Node && !control.current?.contains(event.target)) dismiss();
    };
    document.addEventListener("keydown", onEscape);
    document.addEventListener("pointerdown", onOutside, true);
    return () => {
      document.removeEventListener("keydown", onEscape);
      document.removeEventListener("pointerdown", onOutside, true);
    };
  }, [dismiss, open]);

  return (
    <aside className="screen-help" aria-label={`${title} 이용 안내`}>
      <p className="screen-help-summary">{summary}</p>
      <div
        className="screen-help-control"
        ref={control}
        onPointerEnter={(event) => { if (event.pointerType === "mouse") setHovered(true); }}
        onPointerLeave={(event) => { if (event.pointerType === "mouse") setHovered(false); }}
      >
        <button
          type="button"
          className="screen-help-button"
          aria-controls={panel}
          aria-describedby={open ? panel : undefined}
          aria-expanded={open}
          onFocus={(event) => { if (event.currentTarget.matches(":focus-visible")) setFocused(true); }}
          onBlur={() => setFocused(false)}
          onClick={() => { if (pinned) dismiss(); else setPinned(true); }}
        >
          화면 도움말
        </button>
        <div className="screen-help-panel" id={panel} hidden={!open}>
          <ul>{details.map((detail) => <li key={detail}>{detail}</li>)}</ul>
        </div>
      </div>
    </aside>
  );
}

export default function ScreenHelp({ id }: { id: ScreenHelpId }) {
  return <ScreenHelpCard id={id} key={id} />;
}
