# MILG 위젯 엔진 보완 · 2026-11-11

실제 Python 엔진의 추론 결과를 받는 독립 위젯을 추가했다. MES 시연 화면의 저장 결과를 보여주는 기존 위젯과 별개로 고객 MES에 넣을 수 있는 Web Component다.

## 수정 사항

- 공구별 변화량은 해당 작업 **이전 기록**으로 계산한다. 처음 10개 기록을 모은 뒤 그 기준을 고정하며, 과거 특징이 미래 자료 때문에 바뀌지 않는다. 이 기준 자체를 현장 정상 보증으로 간주하지 않는다.
- 교차검증에서 칼럼 해석·정답 유사 칼럼 제외는 학습 구간만 사용한다. 시험 정답을 바꿔도 학습/시험 입력은 동일해야 한다.
- 결측값 보정·칼럼 유지·표준화는 학습 구간에서 확정한다. 비교용 모델도 같은 원칙을 따른다.
- MCT 알림 기준은 별도 검증 공구에서 정하고 최종 시험 공구에는 그대로 적용한다.
- 새 사출 후보는 공정별 학습·검증·시험을 분리한다. 후보의 입력 개수/정규화 강도는 검증 구간에서만 선택한다. 운영 등록은 AUC뿐 아니라 검출률·오경보·검증 표본을 함께 확인한다.
- `widget_numeric_v1` 추론 어댑터는 평가 게이트가 없는/실패한 후보의 등록을 거부한다. 학습 파일 저장을 운영 적용으로 표시하지 않는다.
- 실제 위젯은 요청한 작업 ID·공정과 응답을 대조한다. 요청 실패/시간 초과 시 이전 작업의 근거를 지운다.
- 조사·보류는 서버의 별도 `reviews` 테이블에 저장한다. 검사 합불 `feedback`과 분리하며 자동 학습하지 않는다.
- pandas 3의 Arrow 문자열 인덱스 역직렬화 호환성을 보완했다. Windows 서버 감사 로그 파일 잠금도 지원한다.

## 다시 계산한 결과

| 대상 | 결과 | 의미 |
|---|---|---|
| MCT NASA 시연 | AUC 0.952 · 교체 기준 도달 13개 중 9개 선행 감지 · 정상 가공 알림 12% | 시간순 특징·공구별 세 구간 평가. 실제 공장 검증이 아님. |
| 사출 새 후보 | AUC 0.8989 · 불량 검출 77.4% · 정상 알림 22.0% | 오경보 기준 10%를 넘겨 **운영 등록 안 함**. |

사출 보고서: `ai/reports/widget-engine/20261111-084233-407047/report.json`.
공개 데이터로 반복 개발한 벤치마크이며 밀봉된 신규 외부 시험이 아니다. 기존 AUC와 같은 조건의 단순 전후 비교가 아니다. 과거 비교표는 이전 평가임을 표시했다.

## PC에서 실제 위젯 열기

PowerShell:

```powershell
cd "C:\Users\dksgu\Desktop\code\mlng-mes\mlng-mes\ai"
python -X utf8 -m mlng_ai.pilot serve --state state --port 8765
```

`http://127.0.0.1:8765`에서 `state/pilot-token.txt`의 토큰을 입력하고 활성 release의 `examples` JSON으로 분석한다. 기존 엔진에 등록된 공정에 대해 실제 추론 결과가 우측 하단 위젯에 표시된다. 사출 후보는 보류 상태이므로 지원 공정으로 가장하지 않는다.

GitHub Pages는 정적 데모를 제공한다. 이 PC의 비공개 엔진을 외부에 자동 공개하지 않는다.

## 고객 MES에 넣기

```html
<script src="/milg-widget.js"></script>
<milg-ai-widget id="milg"></milg-ai-widget>
<script>
const widget = document.getElementById('milg');
widget.configure({
  endpoint: location.origin, // MES가 접근하는 인증된 엔진/리버스 프록시
  token: sessionEngineToken, // 호스트가 세션 중 제공. 소스/로컬 저장소에 넣지 않음
  actor: currentOperator,
  featureLabels: { measured_force: '측정 힘' },
  units: { measured_force: 'N' }
});
await widget.analyze({
  process: selectedRegisteredProcess,
  cycle_id: uniquePhysicalCycleId,
  lot_id: selectedLot,
  context: {machine_id: machineId, part_number: partNumber, program: programId},
  rows: [contractedCycleMeasurements]
});
</script>
```

`process`와 센서 입력 규격은 엔진 `/api/status`와 해당 공정 `examples`를 따른다. 같은 작업 ID 재전송은 동일 입력일 때만 허용한다. 다른 출처에 붙이면 서버에 `--allow-origin https://customer-mes.example`를 명시한다. 엔진은 기본적으로 루프백에만 바인딩하며, 원격 연결은 HTTPS 프록시를 사용한다. 토큰이 있어도 허용되지 않은 웹 출처의 요청은 거부한다.

## 재학습·검증

`tools/Train-MilgWidgetEngine.ps1`은 보관된 `inj_quality.csv`로 후보 학습·검증·시험·기준 통과 시 등록을 수행한다. 현재 보유 자료에서는 등록 기준을 통과하지 못하므로 실행해도 실패 후보를 운영에 덮어쓰지 않는다.

회귀 테스트: `ai/tests/test_widget_engine.py`, `ai/tests/test_pilot.py`.
실제 엔진 연결 테스트: `node tests/widget-live.test.js` (Windows Edge 사용). 이 테스트는 독립 임시 상태 디렉터리에서 실행하며 운영 DB를 수정하지 않는다.
