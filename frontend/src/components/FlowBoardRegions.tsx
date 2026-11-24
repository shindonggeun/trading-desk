export default function FlowBoardRegions({ region }: { region: "kr" | "us" }) {
  return (
    <nav className="page chips" aria-label="수급 현황판 지역">
      <a className={region === "kr" ? "chip chip-on" : "chip"} href="#/flow-board/kr"
        aria-current={region === "kr" ? "page" : undefined}>국장</a>
      <a className={region === "us" ? "chip chip-on" : "chip"} href="#/flow-board/us"
        aria-current={region === "us" ? "page" : undefined}>미장</a>
    </nav>
  );
}
