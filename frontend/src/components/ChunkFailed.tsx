/**
 * #537 화면 · 구역 코드를 받지 못했을 때(배포로 옛 조각이 사라졌거나 연결이 끊겼을 때) — 앱 전체 대신 이 자리만 안내하고 새로 고침을 권한다.
 */
export default function ChunkFailed() {
  return (
    <div className="chunk-failed" role="alert">
      <p className="small">이 부분을 불러오지 못했습니다. 새 버전이 올라왔거나 연결이 잠시 끊겼을 수 있습니다.</p>
      <button type="button" className="small" onClick={() => window.location.reload()}>새로 고침</button>
    </div>
  );
}
