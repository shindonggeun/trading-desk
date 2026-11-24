import { api } from "../api";
import KrFlowBoardScreen from "../components/KrFlowBoardScreen";

export default function KrFlowBoard() {
  return (
    <>
      <nav className="page chips" aria-label="수급 현황판 지역">
        <a className="chip chip-on" href="#/flow-board/kr" aria-current="page">국장</a>
        <button type="button" className="chip" disabled>미장 · 준비 중</button>
      </nav>
      <KrFlowBoardScreen load={api.krFlowBoard} />
    </>
  );
}
