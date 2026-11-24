import { api } from "../api";
import MarketWatchboardScreen from "../components/MarketWatchboardScreen";

export default function MarketWatchboard() {
  return <MarketWatchboardScreen load={api.marketWatchboard} />;
}
