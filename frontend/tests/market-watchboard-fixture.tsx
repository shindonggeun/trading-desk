import { createRoot } from "react-dom/client";
import "../src/styles.css";
import MarketWatchboardScreen, { type LoadWatchboard } from "../src/components/MarketWatchboardScreen";
import { watchboardFixtures, watchboardJapanBreak } from "./market-watchboard-data.mjs";

const options = new URLSearchParams(location.search);
let failCrypto = options.has("failCrypto");
const load: LoadWatchboard = async (group) => {
  if (group === "crypto" && failCrypto) {
    failCrypto = false;
    throw new Error("synthetic network failure");
  }
  return group === "semiconductors" && options.has("japanBreak") ? watchboardJapanBreak : watchboardFixtures[group];
};

createRoot(document.getElementById("root")!).render(<MarketWatchboardScreen load={load} />);
