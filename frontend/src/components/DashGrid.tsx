import type { KeyboardEvent, ReactNode } from "react";
import GridLayout, { verticalCompactor, type Layout } from "react-grid-layout";
import "react-grid-layout/css/styles.css";
import { GAP_PX, GRID_COLS, MIN_W, ROW_PX } from "../dashLayout";

// #487 대시보드 격자 — 배치를 편집하거나 저장한 배치가 있을 때만 불러온다(react-grid-layout 을 첫 화면 번들에서 뺀다).
// #519 세로 여백은 0(간격은 칸 아래 패딩) — 높이가 4px 단위로 내용에 붙는다. 높이가 바뀌면 verticalCompactor 가 위로 붙인다.
export default function DashGrid({ width, items, fixed, editing, onLayoutChange, onHeightSet, renderItem, onKey, label }: {
  width: number;
  items: Layout;
  /** 높이를 사용자가 정한 구역(안쪽 스크롤) — 나머지는 내용에 맞춘 높이 */
  fixed: string[];
  editing: boolean;
  onLayoutChange: (l: Layout) => void;
  /** 모서리로 높이를 바꿨다(폭만 바꾸면 부르지 않는다) — 그 구역을 고정으로 */
  onHeightSet: (id: string) => void;
  renderItem: (id: string) => ReactNode;
  onKey: (id: string) => (e: KeyboardEvent<HTMLDivElement>) => void;
  label: (id: string) => string;
}) {
  return (
    <GridLayout width={width} layout={items.map((it) => ({ ...it, minW: MIN_W }))}
      gridConfig={{ cols: GRID_COLS, rowHeight: ROW_PX, margin: [GAP_PX, 0], containerPadding: [0, 0] }}
      dragConfig={{ enabled: editing, handle: ".dash-drag" }}
      resizeConfig={{ enabled: editing, handles: ["se", "e", "s"] }}
      compactor={verticalCompactor}
      onResizeStop={(_l, oldItem, newItem) => { if (editing && oldItem && newItem && oldItem.h !== newItem.h) onHeightSet(newItem.i); }}
      onLayoutChange={(l) => { if (editing) onLayoutChange(l.map(({ i, x, y, w, h }) => ({ i, x, y, w: Math.max(MIN_W, w), h }))); }}>
      {items.map((it) => (
        <div key={it.i} className={`dash-grid-item${fixed.includes(it.i) ? " dash-fixed" : ""}`} tabIndex={editing ? 0 : undefined} onKeyDown={editing ? onKey(it.i) : undefined}
          role={editing ? "group" : undefined} aria-roledescription={editing ? "옮길 수 있는 구역" : undefined}
          aria-label={editing ? label(it.i) : undefined}>
          {renderItem(it.i)}
        </div>
      ))}
    </GridLayout>
  );
}
