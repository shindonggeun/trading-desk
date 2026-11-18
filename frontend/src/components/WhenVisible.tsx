import { useEffect, useRef, useState, type ReactNode } from "react";

/** 이 이벤트가 오면 기다리던 구역을 모두 그린다 — 안내 투어처럼 화면 밖 요소를 가리켜야 할 때 */
export const REVEAL_ALL_EVENT = "desk:reveal-all";

/**
 * #537 화면 밖 구역은 보일 때 그린다 — 그리기 전엔 API 도 부르지 않는다. 한 번 보이면 계속 둔다(갔다 와도 다시 받지 않게).
 * 화면 아래 margin 만큼 미리 그려 스크롤할 때 빈칸이 보이지 않게 한다. IntersectionObserver 가 없으면 바로 그린다.
 */
export default function WhenVisible({ children, margin = "400px", eager = false, placeholderPx }: {
  children: ReactNode;
  margin?: string;
  /** 처음부터 그린다(알림이 이 구역을 열라고 했을 때 등) */
  eager?: boolean;
  /** #644 기다리는 동안의 높이 — 그려질 높이와 비슷하게 두면 아래 구역들이 처음에 한꺼번에 화면(+ margin) 안으로
   *  끌려 올라와 다 같이 불리지 않는다(짧은 자리표시가 쌓여 30개 넘는 요청이 첫 화면에 몰리던 것) */
  placeholderPx?: number;
}) {
  const box = useRef<HTMLDivElement>(null);
  const [seen, setSeen] = useState(() => eager || typeof IntersectionObserver === "undefined");
  useEffect(() => {
    if (eager) setSeen(true);
  }, [eager]);
  useEffect(() => {
    if (seen) return;
    const all = () => setSeen(true);
    window.addEventListener(REVEAL_ALL_EVENT, all);
    return () => window.removeEventListener(REVEAL_ALL_EVENT, all);
  }, [seen]);
  useEffect(() => {
    if (seen || !box.current) return;
    const io = new IntersectionObserver((entries) => {
      if (entries.some((e) => e.isIntersecting)) setSeen(true);
    }, { rootMargin: `${margin} 0px` });
    io.observe(box.current);
    return () => io.disconnect();
  }, [seen, margin]);
  if (seen) return <>{children}</>;
  return (
    <div ref={box} className="when-visible-wait" aria-busy="true" style={placeholderPx ? { minHeight: placeholderPx } : undefined}>
      <p className="muted small">보이면 불러옵니다…</p>
    </div>
  );
}
