import { useEffect, useRef, useState } from "react";
import { price as fmt } from "./format";
import type { Plan } from "./types";

/** 현재가가 계획의 진입 · 손절 · 목표(와 롱 · 숏 조건)에 닿았을 때의 알림 한 건(#42). */
export interface PriceAlert {
  key: string;
  label: string;
  level: number;
  price: number;
  at: string;
}

type Triggers = { long_trigger?: number | null; short_trigger?: number | null };

function levelsOf(plan: Plan | null): { key: string; label: string; level: number }[] {
  if (!plan) return [];
  const t = plan as Plan & Triggers;
  return [
    { key: "entry", label: "진입가", level: plan.entry_price },
    { key: "stop", label: "손절가", level: plan.stop_loss },
    { key: "target", label: "목표가", level: plan.price_target },
    { key: "long", label: "롱 조건", level: t.long_trigger ?? null },
    { key: "short", label: "숏 조건", level: t.short_trigger ?? null },
  ].filter((l): l is { key: string; label: string; level: number } => l.level != null);
}

export function notificationsAllowed(): boolean {
  return typeof Notification !== "undefined" && Notification.permission === "granted";
}

/**
 * 가격이 레벨을 지나가면(직전 가격과 지금 가격 사이에 레벨이 있으면) 한 번 알린다 — 같은 레벨은
 * 화면을 연 동안 다시 알리지 않는다. 브라우저 알림을 허용했으면 시스템 알림도 띄운다.
 * ``id`` 가 바뀌면(다른 종목 · 다른 계획) 처음부터 다시 센다.
 */
export function usePriceAlerts(id: string, title: string, plan: Plan | null, price: number | null): PriceAlert[] {
  const [alerts, setAlerts] = useState<PriceAlert[]>([]);
  const prev = useRef<number | null>(null);
  const fired = useRef<Set<string>>(new Set());

  useEffect(() => {
    setAlerts([]);
    prev.current = null;
    fired.current = new Set();
  }, [id]);

  useEffect(() => {
    if (price == null) return;
    const before = prev.current;
    prev.current = price;
    if (before == null || before === price) return;
    const lo = Math.min(before, price);
    const hi = Math.max(before, price);
    const hits = levelsOf(plan).filter((l) => l.level >= lo && l.level <= hi && !fired.current.has(l.key));
    if (!hits.length) return;
    const at = new Date().toISOString();
    for (const h of hits) {
      fired.current.add(h.key);
      if (notificationsAllowed()) {
        try {
          new Notification(`${title} ${h.label} 도달`, { body: `현재가 ${fmt(price)} · ${h.label} ${fmt(h.level)}`, tag: `${id}-${h.key}` });
        } catch {
          /* 일부 브라우저는 페이지에서 Notification 생성을 막는다 — 화면 알림만 남긴다 */
        }
      }
    }
    setAlerts((list) => [...hits.map((h) => ({ ...h, price, at })), ...list].slice(0, 10));
  }, [id, title, plan, price]);

  return alerts;
}
