import { api } from "../api";
import FlowBoardRegions from "../components/FlowBoardRegions";
import UsOptionsDistribution from "../components/UsOptionsDistribution";

export default function UsFlowBoard() {
  return (
    <>
      <FlowBoardRegions region="us" />
      <main className="page">
        <section className="card">
          <h1>미장 수급 현황판</h1>
          <UsOptionsDistribution loadOptions={api.usOptions} />
        </section>
      </main>
    </>
  );
}
