import type { KeyboardEvent, ReactNode } from "react";
import GridLayout, { verticalCompactor, type Layout } from "react-grid-layout";
import "react-grid-layout/css/styles.css";
import { GAP_PX, GRID_COLS, MIN_W, ROW_PX } from "../dashLayout";

// #487 대시보드 격자 — 배치를 편집하거나 저장한 배치가 있을 때만 불러온다(react-grid-layout 을 첫 화면 번들에서 뺀다).
export default function DashGrid({ width, items, editing, onLayoutChange, renderItem, onKey, label }: {
  width: number;
  items: Layout;
  editing: boolean;
  onLayoutChange: (l: Layout) => void;
  renderItem: (id: string) => ReactNode;
  onKey: (id: string) => (e: KeyboardEvent<HTMLDivElement>) => void;
  label: (id: string) => string;
}) {
  return (
    <GridLayout width={width} layout={items}
      gridConfig={{ cols: GRID_COLS, rowHeight: ROW_PX, margin: [GAP_PX, GAP_PX], containerPadding: [0, 0] }}
      dragConfig={{ enabled: editing, handle: ".dash-drag" }}
      resizeConfig={{ enabled: editing, handles: ["se", "e", "s"] }}
      compactor={verticalCompactor}
      onLayoutChange={(l) => { if (editing) onLayoutChange(l.map(({ i, x, y, w, h }) => ({ i, x, y, w: Math.max(MIN_W, w), h }))); }}>
      {items.map((it) => (
        <div key={it.i} className="dash-grid-item" tabIndex={editing ? 0 : undefined} onKeyDown={editing ? onKey(it.i) : undefined}
          aria-label={editing ? label(it.i) : undefined}>
          {renderItem(it.i)}
        </div>
      ))}
    </GridLayout>
  );
}
