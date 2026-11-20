import { useEffect, useState } from "react";
import { api, ApiError, type HoldingTicker, type HoldingWatch, type HoldingWatchBody } from "../api";
import { HoldingBadge } from "../holdingWatch";

/**
 * #714 설정 → 보유 종목 알림(#694): 보유 종목마다 일봉 장세(추세 · 박스권 · 전환)로 손절선 · 익절 후보를 잡고,
 * 넘거나 닿으면 웹 푸시 · 텔레그램 · 카카오톡으로. 켜기 전까지 꺼짐, 바꿀 수 있는 사람은 데스크 주인뿐.
 * 알림에는 종목 · 가격 · % 만 — 수량 · 금액 · 평단은 보내지 않는다.
 */
export default function HoldingWatchSettings() {
  const [w, setW] = useState<HoldingWatch | null>(null);
  const [missing, setMissing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [names, setNames] = useState<{ ticker: string; name: string }[]>([]);
  const [noHoldings, setNoHoldings] = useState(false);   // 보유 목록을 못 읽음(토스 장애 등)
  const [loss, setLoss] = useState("");

  useEffect(() => {
    let alive = true;
    api.holdingWatch().then(
      (v) => { if (alive) { setW(v); setLoss(String(v.max_loss_pct)); } },
      (e) => alive && (e instanceof ApiError && e.status === 404 ? setMissing(true) : setNote("설정을 불러오지 못했어요.")),
    );
    // 종목별 설정을 고를 목록 — 보유 종목의 티커 · 이름만 쓴다
    api.portfolio().then(
      (p) => alive && setNames(p.holdings.map((h) => ({ ticker: h.yahoo_ticker, name: h.name }))),
      () => alive && setNoHoldings(true),
    );
    return () => { alive = false; };
  }, []);

  if (missing) return null;   // 서버에 #694 가 아직 없다
  if (!w) {
    return (
      <section className="card settings-card">
        <h2>보유 종목 알림</h2>
        <p className="muted small">{note ?? "불러오는 중…"}</p>
      </section>
    );
  }
  const [lo, hi] = w.limits.max_loss_pct;
  const locked = !w.can_edit || busy;

  const save = async (body: HoldingWatchBody, done?: string) => {
    const before = w;
    // 누른 대로 먼저 보이고(저장은 뒤에서), 실패하면 되돌린다
    const tickers = { ...w.tickers };
    for (const [t, v] of Object.entries(body.tickers ?? {})) {
      if (v === null) delete tickers[t];
      else tickers[t] = { ...own(t), ...v };
    }
    setW({ ...w, ...body, tickers });
    setBusy(true);
    setNote(null);
    try {
      const got = await api.saveHoldingWatch(body);
      setW(got);
      setLoss(String(got.max_loss_pct));
      if (done) setNote(done);
    } catch (e) {
      setW(before);
      setLoss(String(before.max_loss_pct));
      setNote(e instanceof ApiError && e.status === 422 ? `최대 손실은 ${lo}~${hi}% 사이로 적어 주세요.` : "저장하지 못했어요. 잠시 뒤 다시 해 주세요.");
    } finally {
      setBusy(false);
    }
  };
  const lossOf = (raw: string): number | null => {
    const v = Number(raw);
    return raw.trim() !== "" && Number.isFinite(v) && v >= lo && v <= hi ? v : null;
  };
  const saveLoss = () => {
    const v = lossOf(loss);
    if (v === null) { setLoss(String(w.max_loss_pct)); return setNote(`최대 손실은 ${lo}~${hi}% 사이로 적어 주세요.`); }
    if (v !== w.max_loss_pct) void save({ max_loss_pct: v }, `최대 손실을 ${v}% 로 바꿨어요.`);
  };
  const own = (t: string): HoldingTicker => w.tickers[t] ?? { enabled: true, max_loss_pct: null };
  const lines = new Map(w.holdings.map((l) => [l.ticker, l]));
  // 지금 보유한 종목 + 예전에 따로 정해 둔 종목(지금은 없어도 설정은 남는다)
  const rows = [...names, ...Object.keys(w.tickers).filter((t) => !names.some((n) => n.ticker === t)).map((t) => ({ ticker: t, name: t }))];

  return (
    <section className="card settings-card holding-watch">
      <h2>보유 종목 알림</h2>
      <p className="muted">
        보유 종목마다 일봉 장세(상승 · 하락 추세, 박스권, 전환)를 보고 손절선 · 익절 후보를 잡아, 장중에 손절선에 가까워지거나 넘으면,
        하락 추세로 바뀌면, 박스권 위 끝에 닿으면 알려 드려요. 웹 푸시 · 텔레그램 · 카카오톡의 "보유 종목 손절 · 익절" 종류가 켜져 있어야 받아요.
      </p>
      <label className="line-watch-on">
        <input type="checkbox" checked={w.enabled} disabled={locked}
          onChange={() => save({ enabled: !w.enabled }, w.enabled ? "보유 종목 알림을 껐어요." : "보유 종목 알림을 켰어요.")} />
        <strong>보유 종목 알림 받기</strong> <span className="muted small">{w.enabled ? "켜짐" : "꺼짐 — 켜기 전까지는 아무것도 보내지 않아요"}</span>
      </label>

      <label className="hw-loss">
        최대 손실(평단 대비)
        <input type="number" inputMode="decimal" min={lo} max={hi} step={0.5} value={loss} disabled={locked}
          onChange={(e) => setLoss(e.target.value)} onBlur={saveLoss} onKeyDown={(e) => e.key === "Enter" && saveLoss()} />
        <span>%</span>
      </label>
      <p className="muted small">
        손절선은 이 손실선과 장세별 구조선(추세: 최근 22봉 고점 − 4 ATR · 박스권: 하단 − 1 ATR · 그 밖: 최근 40봉 저점 − 1 ATR) 중 먼저 닿는 쪽이에요.
        손절 알림은 하루 상한 없이 바로, 나머지는 종목마다 하루 한 번이에요. 매수 · 매도 추천이 아니에요.
      </p>

      <h3>종목별</h3>
      {noHoldings && <p className="muted small">보유 종목을 불러오지 못했어요. 따로 정해 둔 종목만 보여요.</p>}
      {rows.length === 0 ? (!noHoldings && <p className="muted small">보유 종목이 없어요.</p>) : (
        <ul className="hw-tickers">
          {rows.map(({ ticker, name }) => {
            const o = own(ticker);
            const line = lines.get(ticker);
            return (
              <li key={ticker}>
                <label className="hw-ticker-on">
                  <input type="checkbox" checked={o.enabled} disabled={locked}
                    onChange={() => save({ tickers: { [ticker]: { enabled: !o.enabled } } })} />
                  <strong>{name}</strong> <span className="muted small">{ticker}</span>
                </label>
                <label className="hw-loss small">
                  최대 손실
                  <input type="number" inputMode="decimal" min={lo} max={hi} step={0.5} placeholder={String(w.max_loss_pct)}
                    defaultValue={o.max_loss_pct ?? ""} key={`${ticker}-${o.max_loss_pct ?? ""}`} disabled={locked || !o.enabled}
                    aria-label={`${name} 최대 손실 %`}
                    onKeyDown={(e) => e.key === "Enter" && e.currentTarget.blur()}
                    onBlur={(e) => {
                      const raw = e.target.value;
                      if (e.target.validity.badInput) {      // "1e" · "-": 비운 것과 다르다 — 되돌리고 안내만
                        e.target.value = o.max_loss_pct == null ? "" : String(o.max_loss_pct);
                        return setNote(`최대 손실은 ${lo}~${hi}% 사이로 적어 주세요.`);
                      }
                      if (raw.trim() === "") { if (o.max_loss_pct != null) void save({ tickers: { [ticker]: { max_loss_pct: null } } }); return; }
                      const v = lossOf(raw);
                      if (v === null) { e.target.value = o.max_loss_pct == null ? "" : String(o.max_loss_pct); return setNote(`최대 손실은 ${lo}~${hi}% 사이로 적어 주세요.`); }
                      if (v !== o.max_loss_pct) void save({ tickers: { [ticker]: { max_loss_pct: v } } });
                    }} />
                  <span>%</span>
                </label>
                {w.tickers[ticker] && (
                  <button type="button" className="link small" disabled={locked} onClick={() => save({ tickers: { [ticker]: null } })}>
                    전체 설정 따르기
                  </button>
                )}
                {w.enabled && line && <HoldingBadge line={line} />}
              </li>
            );
          })}
        </ul>
      )}
      {!w.can_edit && <p className="muted small">설정은 데스크 주인만 바꿀 수 있어요.</p>}
      {note && <p className="settings-notice" role="status">{note}</p>}
    </section>
  );
}
