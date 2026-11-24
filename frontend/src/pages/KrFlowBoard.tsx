import { api } from "../api";
import FlowBoardRegions from "../components/FlowBoardRegions";
import KrFlowBoardScreen from "../components/KrFlowBoardScreen";

export default function KrFlowBoard() {
  return (
    <>
      <FlowBoardRegions region="kr" />
      <KrFlowBoardScreen load={api.krFlowBoard} />
    </>
  );
}
