/**
 * #762 사람이 화면 앞에 없는 동안의 요청(자동 다시 읽기 · 타이머 · 스트림 재연결 뒤의 읽기)은 세션 활동이 아니다.
 * 마지막 입력(누르기 · 키 · 휠 · 터치 · 클릭 — 키보드 · 보조 기기가 만드는 click 포함)에서 ACTIVE_MS 가 지난 요청에 `X-Desk-Background: 1` 을 붙인다.
 * 서버는 그 요청도 똑같이 세션을 확인하지만 마지막 활동 시각(last_seen_at)은 옮기지 않는다 — 열어 둔 탭도 유휴 시간이 지나면 만료된다.
 * 머리글은 세션을 줄이는 쪽으로만 쓰이므로(활동에서 빼 달라는 표시) 꾸며 보내도 얻는 것이 없다. docs/security.md §2.
 * 화면을 연 것(페이지 로드)도 입력으로 친다.
 */

export const BACKGROUND_HEADER = "X-Desk-Background";
/** 마지막 입력 뒤 이만큼은 요청을 사람이 한 것으로 본다 — 누른 뒤 이어지는 읽기 · 화면 전환이 넉넉히 들어가게 */
export const ACTIVE_MS = 120_000;

let lastInput = Date.now();

export function noteInput(at = Date.now()): void {
  lastInput = at;
}

if (typeof window !== "undefined") {
  for (const type of ["pointerdown", "keydown", "wheel", "touchstart", "click"] as const) {
    window.addEventListener(type, () => noteInput(), { capture: true, passive: true });
  }
}

/** 지금 보내는 요청에 붙일 머리글 — 사람이 있으면 없음 */
export function activityHeaders(now = Date.now()): Record<string, string> {
  return now - lastInput > ACTIVE_MS ? { [BACKGROUND_HEADER]: "1" } : {};
}
