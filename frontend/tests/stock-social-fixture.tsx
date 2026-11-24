import { useState } from "react";
import { createRoot } from "react-dom/client";
import "pretendard/dist/web/variable/pretendardvariable-dynamic-subset.css";
import "../src/styles.css";
import StockProfileCard from "../src/components/StockProfileCard";

function Fixture() {
  const [ticker, setTicker] = useState("SOXX");
  return (
    <main style={{ maxWidth: 920, margin: "16px auto", paddingInline: 12 }}>
      <nav aria-label="검증 종목" style={{ display: "flex", flexWrap: "wrap", gap: 4, marginBottom: 8 }}>
        {["SOXX", "SOXL", "NVDA", "AAPL"].map((symbol) => (
          <button key={symbol} type="button" onClick={() => setTicker(symbol)}>{symbol}</button>
        ))}
      </nav>
      <StockProfileCard ticker={ticker} />
    </main>
  );
}

createRoot(document.getElementById("root")!).render(<Fixture />);
