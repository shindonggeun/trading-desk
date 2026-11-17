import { toggleFavorite, useFavorites } from "../favorites";
import { sameTicker } from "../ticker";

/** 즐겨찾기 별(#135) — ☆ 빈 별 / ★ 즐겨찾기. 누르면 바로 바뀐다. */
export default function FavoriteStar({ ticker, name, className }: { ticker: string; name?: string | null; className?: string }) {
  const { list } = useFavorites();
  const t = ticker.toUpperCase();
  const on = !!list?.some((f) => sameTicker(f.ticker, t));
  const label = `${name ?? ticker} 즐겨찾기${on ? " 빼기" : " 추가"}`;
  return (
    <button type="button" className={`fav-star${on ? " fav-on" : ""}${className ? ` ${className}` : ""}`}
      aria-pressed={on} aria-label={label} title={label} disabled={list === null}
      onClick={(e) => {
        e.stopPropagation();
        toggleFavorite(t, name);
      }}>
      <span aria-hidden="true">{on ? "★" : "☆"}</span>
    </button>
  );
}
