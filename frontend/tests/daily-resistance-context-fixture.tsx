import { useState } from "react";
import { createRoot } from "react-dom/client";
import PriceChart from "../src/components/PriceChart";
import "../src/styles.css";

function Fixture() {
  const [ticker, setTicker] = useState("SOXX");
  return (
    <main style={{ margin: "16px auto", maxWidth: 1080, paddingInline: 12 }}>
      <nav aria-label="검증 종목">
        {["SOXX", "SOXL", "NQ=F", "^NDX", "SOXLUSDT.P"].map((symbol) => (
          <button type="button" key={symbol} onClick={() => setTicker(symbol)}>{symbol}</button>
        ))}
      </nav>
      <PriceChart key={ticker} ticker={ticker} tradeDate="2026-11-23" plan={null} initialInterval="1d"
        title={ticker} lists={false} readable readOpen />
    </main>
  );
}

createRoot(document.getElementById("root")!).render(<Fixture />);
