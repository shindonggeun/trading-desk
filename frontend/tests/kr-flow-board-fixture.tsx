import { createRoot } from "react-dom/client";
import "../src/styles.css";
import KrFlowBoardScreen, { type LoadKrFlowBoard } from "../src/components/KrFlowBoardScreen";
import { flowFixtures } from "./kr-flow-board-data.mjs";

const holdKospi = new URLSearchParams(location.search).has("holdKospi");
const load: LoadKrFlowBoard = async (market) => {
  if (market === "kospi" && holdKospi) {
    await new Promise<void>((resolve) => window.addEventListener("fixture:release-kospi", () => resolve(), { once: true }));
  }
  return flowFixtures[market];
};

createRoot(document.getElementById("root")!).render(<KrFlowBoardScreen load={load} />);
