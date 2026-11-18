import { useEffect, useState } from "react";
import { api, ApiError, type NotifyChannel, type NotifyChannelId, type NotifyChannels as Channels } from "../api";
import { EXTRA_KINDS } from "./FlowAlertToast";

/** #581 알림 채널(#580) — 텔레그램 · 카카오톡 "나에게 보내기". 켬/끔 · 받을 종류 · 시험 보내기 · 카카오 연결.
 *  웹 푸시는 기기마다 따로라 위 "이 기기 푸시" 칸이 맡는다. 서버에 #580 이 없으면(404) "준비 중" 으로만 보인다. */
const KIND_LABEL: Record<string, string> = {
  flow: "수급 전향", ...Object.fromEntries(EXTRA_KINDS.map((k) => [k.key, k.label])),
};
// 채널에서 고르게 할 종류 — 시장별 전향 스위치(flow_kospi …)는 웹 푸시 칸에서만
const SHOWN_KINDS = ["flow", "flow_shift", "nq_alert", "morning_brief", "day_call"];
const PLACEHOLDER: { id: NotifyChannelId; label: string }[] = [{ id: "telegram", label: "텔레그램" }, { id: "kakao", label: "카카오톡" }];
const TEST_ERROR: Record<string, string> = {
  "not configured": "서버에 이 채널 설정이 없습니다",
  "not connected": "아직 연결하지 않았습니다",
  timeout: "응답이 늦어 끊었습니다(150초)",
};

function kakaoReturn(): "ok" | "fail" | null {
  const m = window.location.hash.match(/[?&]kakao=(ok|fail)(?:&|$)/);
  return m ? (m[1] as "ok" | "fail") : null;
}

export default function NotifyChannels() {
  const [data, setData] = useState<Channels | null>(null);
  const [missing, setMissing] = useState(false);
  const [failed, setFailed] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(() => {
    const r = kakaoReturn();
    return r === "ok" ? "카카오톡을 연결했습니다." : r === "fail" ? "카카오톡 연결에 실패했습니다. 다시 시도해 주세요." : null;
  });

  useEffect(() => {
    let alive = true;
    api.notifyChannels()
      .then((d) => {
        if (!alive) return;
        setData(d);
        // #580: 카카오 콜백이 늦으면(504) 실패로 돌아오지만 연결은 이미 됐을 수 있다 — 목록이 말하는 쪽으로
        if (kakaoReturn() === "fail" && d.channels.some((c) => c.id === "kakao" && c.connected && !c.needs_reconnect)) {
          setNotice("카카오톡을 연결했습니다(응답이 늦어 실패로 보였습니다).");
        }
      })
      .catch((e) => {
        if (!alive) return;
        if (e instanceof ApiError && e.status === 404) setMissing(true);
        else setFailed(e instanceof Error ? e.message : "불러오지 못했습니다");
      });
    return () => { alive = false; };
  }, []);

  const replace = (c: NotifyChannel) => setData((d) => d && { ...d, channels: d.channels.map((x) => (x.id === c.id ? c : x)) });
  const act = async (key: string, work: () => Promise<string | void>) => {
    setBusy(key);
    setNotice(null);
    try {
      const said = await work();
      if (said) setNotice(said);
    } catch (e) {
      setNotice(e instanceof ApiError
        ? (e.status === 429 ? "잠시 뒤에 다시 보내 주세요(시험 보내기는 채널 모두 합쳐 5초에 한 번)." : e.status === 403 ? "소유자 계정만 바꿀 수 있습니다." : `실패 — ${e.message}`)
        : "실패 — 잠시 뒤 다시 해 주세요");
    } finally {
      setBusy(null);
    }
  };

  const editable = !!data?.can_edit;
  return (
    <div className="notify-channels">
      <h3>다른 채널</h3>
      <p className="muted small">이 기기 푸시 말고도 텔레그램 · 카카오톡(나에게 보내기)으로 받을 수 있습니다. 채널마다 받을 알림을 고릅니다.</p>
      {missing && PLACEHOLDER.map((c) => (
        <div key={c.id} className="notify-channel">
          <span>{c.label}</span>
          <span className="pill">준비 중</span>
        </div>
      ))}
      {failed && <p className="error small" role="alert">채널 설정을 불러오지 못했습니다. {failed}</p>}
      {!data && !missing && !failed && <p className="muted small">불러오는 중…</p>}
      {data?.channels.map((c) => {
        const ready = c.configured && c.connected;
        const status = !c.configured ? "서버 설정 필요" : !c.connected ? "연결 안 됨" : c.enabled ? "켜짐" : "꺼짐";
        const kinds = SHOWN_KINDS.filter((k) => data.kinds.includes(k));
        return (
          <div key={c.id} className="notify-channel">
            <span>{c.label}</span>
            <span className={`pill${ready && c.enabled ? " pill-on" : !c.configured || c.needs_reconnect ? " pill-warn" : ""}`}>{status}</span>
            {c.id === "kakao" && c.needs_reconnect && (
              <p className="settings-notice notice-warn small" role="status">
                카카오 연결이 곧 끝나거나 갱신이 거절됐습니다. 다시 연결해 주세요.
              </p>
            )}
            {!c.configured && c.id === "telegram" && (
              <p className="muted small">텔레그램은 서버 환경 변수(봇 토큰 · 대화 ID)를 넣어야 켤 수 있습니다.</p>
            )}
            <div className="settings-actions">
              {ready && (
                <button type="button" className={c.enabled ? "secondary" : ""} disabled={!editable || busy !== null}
                  onClick={() => act(`en-${c.id}`, async () => {
                    replace(await api.saveNotifyChannel(c.id, { enabled: !c.enabled }));
                    return c.enabled ? `${c.label} 알림을 껐습니다.` : `${c.label} 알림을 켰습니다.`;
                  })}>{c.enabled ? "끄기" : "켜기"}</button>
              )}
              {ready && (
                <button type="button" className="secondary" disabled={!editable || busy !== null}
                  onClick={() => act(`test-${c.id}`, async () => {
                    const r = await api.testNotifyChannel(c.id);
                    return r.ok ? `${c.label}으로 시험 알림을 보냈습니다.`
                      : `보내지 못했습니다 — ${TEST_ERROR[r.error ?? ""] ?? r.error ?? "알 수 없는 오류"}`;
                  })}>시험 보내기</button>
              )}
              {c.id === "kakao" && c.configured && (!c.connected || c.needs_reconnect) && (
                <button type="button" disabled={!editable || busy !== null}
                  onClick={() => act("kakao", async () => { window.location.href = (await api.kakaoConnect()).url; })}>
                  {c.connected ? "카카오 다시 연결" : "카카오 연결"}
                </button>
              )}
              {c.id === "kakao" && c.connected && (
                <button type="button" className="link" disabled={!editable || busy !== null}
                  onClick={() => act("kakao-off", async () => {
                    await api.kakaoDisconnect();
                    replace({ ...c, connected: false, enabled: false, needs_reconnect: false });
                    return "카카오톡 연결을 끊었습니다.";
                  })}>연결 끊기</button>
              )}
            </div>
            {ready && (
              <div className="push-markets" role="group" aria-label={`${c.label}으로 받을 알림`}>
                {kinds.map((k) => {
                  const on = c.kinds[k] !== false;
                  return (
                    <button key={k} type="button" className={`chip ${on ? "chip-on" : ""}`} aria-pressed={on}
                      disabled={!editable || busy !== null}
                      onClick={() => act(`kind-${c.id}-${k}`, async () => {
                        replace(await api.saveNotifyChannel(c.id, { kinds: { ...c.kinds, [k]: !on } }));
                      })}>
                      <span aria-hidden="true" className="push-mark">{on ? "✓" : "＋"}</span>{KIND_LABEL[k] ?? k}
                      <span className="sr-only">{on ? " 받음" : " 안 받음"}</span>
                    </button>
                  );
                })}
              </div>
            )}
          </div>
        );
      })}
      {data && !editable && <p className="muted small">채널 설정은 소유자 계정만 바꿀 수 있습니다.</p>}
      {notice && <p className="settings-notice" role="status">{notice}</p>}
    </div>
  );
}
