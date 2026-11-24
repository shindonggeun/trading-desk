# TradingView 개인 작도 옮기기

2026-11-24 기준으로 확인한 공개 공식 문서에는 TradingView 개인 레이아웃의 작도를 Trading Desk로 자동 이전할 수 있는 파일 형식이나 API가 명시되어 있지 않다. 수평선·추세선·평행 채널은 원본 차트의 종목, 봉, 가격축을 맞춘 뒤 좌표를 입력해 다시 그릴 수 있다.

## 공식 기능에서 확인한 범위

| 경로 | 옮기거나 저장할 수 있는 내용 | 개인 작도 이전에 적용할 수 있는 범위 |
| --- | --- | --- |
| Supercharts 차트 CSV | 선택한 차트에서 이미 로드된 봉 시각·시가·고가·저가·종가(OHLC)와 지표의 출력 값을 내려받는다. Pine FAQ의 예제에서는 플롯의 제목이 해당 CSV 열 이름이 된다. 내보낼 이력이 부족하면 차트를 왼쪽으로 이동해 데이터를 더 불러온다. | 공개 안내에 작도 앵커·그룹을 담는 열이나 레이아웃 복원 규격이 나오지 않는다. [CSV 도움말](https://www.tradingview.com/support/solutions/43000537255-how-to-export-chart-data/), [Pine CSV 열 예제](https://www.tradingview.com/pine-script-docs/faq/indicators/#is-it-possible-to-export-indicator-data-to-a-file) |
| Supercharts 개인 레이아웃 | 차트·설정·지표·작도를 저장한다. 공유를 켠 레이아웃의 수신자는 보기 전용으로 열고 자신의 TradingView 계정에 복사할 수 있다. | 레이아웃 상호작용 도움말에는 다운로드 표현이 나오지만, 공개 예시는 서비스 안의 `Open layout` 메뉴를 보여 준다. 외부 앱이 복원할 수 있는 파일·스키마나 계정 작도 조회 API는 검토한 문서에서 확인되지 않았다. [레이아웃과 작도](https://www.tradingview.com/support/solutions/43000692404-layouts-charts-drawings-indicators-and-their-interaction/), [보기 전용 공유와 복사](https://www.tradingview.com/support/solutions/43000606515-how-to-share-charts-in-view-only-mode/) |
| Advanced Charts 저장 API | 통합자가 호스팅하는 위젯의 `widget.save()`와 `widget.load()`로 차트 상태를 저장하고 복원한다. 별도 작도 저장을 켜면 `getLineToolsState()`와 `applyLineToolsState()` 또는 자체 저장 서버의 REST/어댑터를 사용한다. | 접근 제어는 통합자의 저장 서버에서 처리한다. REST의 `client`·`user` 값은 저장 범위를 지정하는 식별자다. 작도 상태는 라이브러리가 해석하는 불투명한 데이터이며 Supercharts 계정 작도에 접근하는 계약은 공개되어 있지 않다. [저수준 API](https://www.tradingview.com/charting-library-docs/latest/saving_loading/low-level-api/), [작도 별도 저장](https://www.tradingview.com/charting-library-docs/latest/saving_loading/saving_drawings_separately/), [REST 작도 메서드](https://www.tradingview.com/charting-library-docs/latest/saving_loading/save-load-rest-api/drawing-methods/) |
| TradingView 공식 MCP | OAuth로 연결하며 공식 도구 목록에는 관심종목, 시세 데이터, 알림, Pine 등 계정 기능이 포함된다. | 확인한 공개 도구 목록에는 개인 레이아웃이나 작도를 읽고 쓰는 기능이 없다. [공식 MCP 도구 목록](https://www.tradingview.com/mcp/docs#tools) |

Trading Desk는 [Lightweight Charts](../frontend/package.json)를 사용한다. TradingView는 Advanced Charts와 Trading Platform 라이선스를 공개 웹 프로젝트 또는 앱을 운영하는 기업에 제공한다고 안내한다. 라이브러리 접근은 승인 절차를 거치며 공개 저장소에 배포할 수 없다. 향후 자동 이전을 검토하려면 개인 작도에 대한 공식 접근 권한, 시간·가격 좌표를 포함하는 공개 형식, 재사용 조건을 먼저 확인해야 한다. [제품별 라이선스 안내](https://www.tradingview.com/free-charting-libraries/), [Advanced Charts 설치 안내](https://www.tradingview.com/charting-library-docs/latest/quick-start/)

## 좌표를 직접 옮기는 순서

원본을 보면서 비교하려면 본인 TradingView 레이아웃을 다른 탭에 열어 둔다. 다른 사람에게 보여 줄 때는 레이아웃 메뉴에서 공유를 켜고 주소를 복사한다. 링크를 받은 사람은 TradingView에 로그인해 차트와 지표를 확인하고 자신의 계정에 복사할 수 있다. 특정 시점의 모양을 남기려면 차트 상단 카메라에서 이미지를 저장하거나 이미지 링크를 복사한다. 링크와 스냅샷은 마감 후 위치나 선의 모양을 맞춰 보는 참고 자료로 사용하고, 좌표는 도형 설정에서 별도로 기록한다. [보기 전용 공유](https://www.tradingview.com/support/solutions/43000606515-how-to-share-charts-in-view-only-mode/), [스냅샷 공유](https://www.tradingview.com/support/solutions/43000482537-how-to-share-a-snapshot/)

1. 양쪽 차트에서 같은 상품·거래소, 시간봉, 세션, 가격 보정과 눈금을 고른다. 예를 들어 Trading Desk의 `SOXX`는 TradingView의 `NASDAQ:SOXX`와 대조한다. `NQ=F`와 `CME_MINI:NQ1!`는 나스닥100 선물 차트를 찾는 출발점이다. 두 원천의 월물 교체 시점과 연속선물 보정이 다를 수 있으므로 원본 월물과 보정 여부가 확인될 때만 과거 봉 위치를 그대로 재현한다. ETF의 배당 보정과 정규장/연장장 선택도 확인한다. [SOXX](https://www.tradingview.com/symbols/NASDAQ-SOXX/), [NQ1!](https://www.tradingview.com/symbols/CME_MINI-NQ1!/), [차트 설정](https://www.tradingview.com/support/solutions/43000748166-how-to-configure-your-supercharts/), [연속선물 보정](https://www.tradingview.com/support/solutions/43000685266-how-can-i-enable-backadjustment-for-continuous-futures/)
2. TradingView에서는 작도 설정의 좌표 탭에서 가격과 봉 번호를 확인하고 해당 봉의 날짜·시각을 기록한다. 봉 번호는 차트 안의 위치이므로 다른 차트에는 날짜·시각으로 대응시킨다. Trading Desk에서는 `수동 작도 → 저장된 작도 → 작도 선택 → 앵커 복사`로 종목·시간봉·도형 이름, 앵커별 원본 봉 시각과 가격을 확인한다. 복사 값에 UTC 오프셋이 있으면 대상 차트의 표시 시간대로 변환한다. 날짜만 있으면 해당 일봉이나 주봉의 거래일 라벨을 확인한다. 대상 차트를 더 불러와도 같은 봉을 찾을 수 없으면 해당 앵커를 옮기는 작업을 보류한다. [앵커 복사 구현](../frontend/src/chartDrawingModel.ts), [TradingView 추세선 좌표](https://www.tradingview.com/support/solutions/43000518095-trendline-drawing-tool/)
3. 대상에서 같은 도형을 고르고 기록한 순서대로 좌표를 입력한다. Trading Desk는 `키보드로 점 지정`에서 로드된 봉과 가격을 선택할 수 있다. TradingView에서는 도형을 추가한 뒤 좌표 설정에서 봉과 가격을 맞춘다. 가격축의 통화·호가 단위와 앵커 값을 확인하고 연장선이나 색상은 필요한 경우 따로 설정한다. [Trading Desk 작도 패널](../frontend/src/components/ChartDrawingPanel.tsx), [TradingView 수평선](https://www.tradingview.com/support/solutions/43000518124-horizontal-line-drawing-tool/), [TradingView 평행 채널](https://www.tradingview.com/support/solutions/43000518117-parallel-channel/)

### 예제

아래 가격과 앵커는 재현용 합성 값이다. Trading Desk의 앵커 복사 형식으로 적은 `SOXX` 5분봉 평행 채널 예제다.

```text
SOXX · 5m · 평행 채널
A 2026-11-23T14:30:00Z · 120 USD
B 2026-11-23T15:00:00Z · 126 USD
C 2026-11-23T14:45:00Z · 127.5 USD
```

| 앵커 | 뉴욕 (EST) | 서울 (KST) |
| --- | --- | --- |
| A | 11월 23일 09:30 | 11월 23일 23:30 |
| B | 11월 23일 10:00 | 11월 24일 00:00 |
| C | 11월 23일 09:45 | 11월 23일 23:45 |

- **수평선**은 A의 가격 120 USD를 입력한다. Trading Desk에서 A는 작도 기준봉으로도 기록된다. TradingView 수평선의 좌표는 가격 하나다. TradingView에서 먼저 그린 수평선을 Trading Desk로 옮길 때는 같은 가격을 입력하고 현재 로드된 봉을 기준으로 선택한다. [수평선 좌표](https://www.tradingview.com/support/solutions/43000518124-horizontal-line-drawing-tool/)
- **추세선**은 A와 B의 봉·가격을 입력한다. 차트 시간대가 서울이면 B가 다음 날짜에 있으므로 날짜까지 맞춘다.
- **평행 채널**은 A와 B로 기준선을 만들고 C로 평행선 위치를 지정한다. 이 세 봉 사이에 결측이 없는 선형 차트에서 C는 A와 B의 중간 봉이다. TradingView에는 A와 B를 레벨 0의 좌표로 입력하고 레벨 1의 `Price offset`을 `+4.5 USD`로 맞춘다. C에서 기준선은 123 USD이고 평행선은 127.5 USD다. Trading Desk로 옮길 때 선형 차트의 오프셋만 알고 있다면 기준선 위의 로드된 봉을 C로 골라 그 봉에서의 기준선 가격에 부호 있는 오프셋을 더한다. [평행 채널 좌표](https://www.tradingview.com/support/solutions/43000518117-parallel-channel-drawing-tool/)

Trading Desk는 주봉·월봉·연봉에 로그 가격축을 사용한다. 로그 축에서는 같은 화면 간격이 가격의 비율에 해당한다. 위 예제에서 로그 축의 중간 봉 기준선은 약 122.96 USD다. 선형 축에서 계산한 `+4.5 USD`를 로그 축의 채널 전체에 적용하면 원본과 달라진다. 양쪽 가격축을 맞추고 C의 봉·가격에서 평행선이 만나는지 직접 확인한다. 다른 세션이나 결측으로 봉 간격이 달라졌다면 중간 봉 예제의 산식을 그대로 쓰지 않는다. [Trading Desk 가격축](../frontend/src/components/PriceChart.tsx), [채널 렌더링](../frontend/src/components/chartDrawingPrimitive.ts)

## 확인한 표본과 검증 범위

- TradingView가 공개한 CSV 선택 창 예시에는 `NASDAQ:AAPL`의 일봉과 5분봉 선택이 표시된다. 레이아웃 공식 화면 예시의 같은 메뉴에는 차트 데이터 내보내기와 레이아웃 열기가 별도로 표시된다. 공개 화면과 안내로 확인할 수 있는 범위까지 대조했으며, 계정에서 내려받은 실제 CSV의 열이나 개인 레이아웃의 저장 데이터는 확인하지 않았다. [CSV 화면 예시](https://www.tradingview.com/support/solutions/43000537255-how-to-export-chart-data/), [레이아웃 메뉴 예시](https://www.tradingview.com/support/solutions/43000692404-layouts-charts-drawings-indicators-and-their-interaction/)
- Advanced Charts 공식 공개 예제는 위젯의 `save_load_adapter`가 브라우저 `localStorage`에 저장하는 구성이다. 공개 예제의 저장 경계를 문서에서 대조했다. 개인 계정 로그인, 비공개 데이터 조회, 라이브러리 설치나 계정 간 가져오기 검증은 수행하지 않았다. [공식 저장 어댑터 예제](https://www.tradingview.com/charting-library-docs/latest/saving_loading/save-load-adapter/#localstorage)
- Trading Desk에서 기존 작도 모델 테스트와 합성 앵커 예제의 복사 출력, 시간대 변환, 선형·로그 계산을 확인했다. 두 서비스 화면 사이의 실제 재작성 검증은 수행하지 않았다.
