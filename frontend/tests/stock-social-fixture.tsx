import { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import "pretendard/dist/web/variable/pretendardvariable-dynamic-subset.css";
import "../src/styles.css";
import StockProfileCard from "../src/components/StockProfileCard";
import Charts from "../src/pages/Charts";

function Fixture() {
  const [ticker, setTicker] = useState("SOXX");
  const [hash, setHash] = useState(window.location.hash);
  useEffect(() => {
    const update = () => setHash(window.location.hash);
    window.addEventListener("hashchange", update);
    return () => window.removeEventListener("hashchange", update);
  }, []);
  if (hash.startsWith("#/charts?")) return <Charts query={new URLSearchParams(hash.split("?")[1])} />;
  return (
    <main style={{ maxWidth: 920, margin: "16px auto", paddingInline: 12 }}>
      <nav aria-label="검증 종목" style={{ display: "flex", flexWrap: "wrap", gap: 4, marginBottom: 8 }}>
        {["SOXX", "SOXL", "NVDA", "AAPL", "005930.KS"].map((symbol) => (
          <button key={symbol} type="button" onClick={() => setTicker(symbol)}>{symbol}</button>
        ))}
      </nav>
      <StockProfileCard ticker={ticker} name={ticker === "SOXX" ? "반도체 ETF" : ticker} />
    </main>
  );
}

createRoot(document.getElementById("root")!).render(<Fixture />);
