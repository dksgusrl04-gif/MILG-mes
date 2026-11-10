# MILG 현장 연결용 파일럿

## Training Pack 공개 데이터 학습

저장소 루트에서 아래 명령으로 다운로드 폴더의 ZIP을 직접 읽습니다.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File tools/Train-MilgProcessPacks.ps1 -Install
```

`-PackDir`로 다른 데이터 폴더를 지정할 수 있습니다. `-Install`을 빼면 보고서와 후보 모델만 저장합니다. 실행 결과 경로는 `ai/reports/process-packs` 아래에 표시됩니다.

- KAMP 사출은 동일 행 제거 후 날짜별로 학습·검증·시험을 분리합니다. 검사 사유와 식별자는 특징에서 제외합니다.
- KAMP 열처리는 배정번호별 센서 요약과 검사 수량으로 불량률을 학습합니다. 공정최적화 ZIP은 센서와 라벨의 중복 확인에 사용합니다. 동봉된 위험·안정 분류표와는 입력 요약을 비교합니다.
- SCATIM은 데이터셋별 중량과 치수 회귀 후보를 따로 저장합니다. 기준을 통과한 중량 후보는 `scatim_2_weight` 또는 `scatim_3_weight` 경로로 등록됩니다. 명시적으로 공정을 선택하고 원본과 같은 단위로 `pressure`, `pressure_time`, `flow`, `flow_time` 배열이나 릴리스 예시의 요약 특징을 전달하세요.
- Copper Rings는 위치·힘을 같은 샘플에서 연결해 기존 압입 입력을 확인합니다. Copper의 설정 오프셋과 Crimp의 제작 조건 라벨로 유사 공정 이상 점수를 따로 평가합니다.

등록은 공개 데이터 평가 기준을 통과한 모델에 한해 이루어집니다. 콘솔이 이미 켜져 있으면 종료한 뒤 `tools/Start-MilgPilot.ps1`로 다시 실행해 새 릴리스를 확인하세요.

이 패키지는 공개 데이터로 학습한 MCT·체결 모델, 압입 힘–변위 특징과 정상 기준 학습, 작업 저장·검사 결과·모델 복구를 제공합니다. 산업용 압입 불량 분류기를 공개 자료만으로 완성했다는 의미는 아닙니다. 실제 불량의 검출 성능은 미검증입니다.

## 설치 및 실행

저장소 루트에서 실행합니다. 기본 상태 폴더는 `ai/state`입니다. 다른 고객은 `-StateDir`에 고객 전용 경로를 지정해야 하며, 고객 사이에 SQLite나 상태 폴더를 복사하지 마세요.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File tools/Prepare-MilgPilot.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File tools/Start-MilgPilot.ps1
```

설치 스크립트는 데이터 경로 탐색 → 수정 체결 체크섬 확인 → 일치하는 학습 후보 검색 → MCT 공구/설비 분리 학습·평가 → 모델 등록 → 격리된 추론·저장 검증 → 활성 릴리스 포인터 교체를 수행합니다. 어느 단계든 실패하면 이전 활성 릴리스가 유지됩니다. 체결 후보가 없다면 먼저 `mlng_ai.train_screw`를 실행해야 합니다. 신규 MCT RAR, MATWI는 이 설치 명령에 포함되지 않습니다.

웹 콘솔: http://127.0.0.1:8765 . 실행 후 `ai/state/pilot-token.txt`를 읽어 콘솔에 입력합니다. 토큰은 브라우저 저장소에 보관하지 않습니다. 이 콘솔은 로컬 연동용입니다. 공장 네트워크 공유는 기존 인증 API·TLS 프록시 환경을 별도로 구성해야 합니다.

```powershell
Get-Content -LiteralPath ai/state/pilot-token.txt
```

API를 호출한 데이터와 검사 근거는 지정한 상태 폴더의 `pilot.sqlite3`에 저장됩니다. 외부 API 호출은 없습니다. 패키지 설치 시 pip만 인터넷을 사용합니다. 서버를 실행한 뒤 릴리스를 다시 설치했다면 콘솔 서버를 재시작하세요.

## 입력과 추론

활성 경로는 `ai/state/pilot-active.json`에 기록됩니다. 해당 릴리스의 `examples` 폴더에 공정별 JSON 예시가 있습니다. 예시는 `demo=true`로 표시해 현장 정상 기준 학습에서 제외합니다. 실제 측정 데이터는 `demo=false`로 제출합니다.

작업마다 유일한 `cycle_id`, `lot_id`, `context.machine_id`, `context.part_number`, `context.program`이 필요합니다. 같은 작업 ID와 같은 입력은 이전 결과를 반환하며 중복 저장하지 않습니다. 같은 ID에 다른 입력을 보내면 오류입니다. 프로그램·재질·금형 조건이 바뀌면 `program`의 버전을 바꾸어 기준을 분리하세요.

| 공정 키 | 입력 | 판정 대상 |
|---|---|---|
| mct_hmotp | 명시된 센서 요약 특징 | 공개 데이터의 마모 임계값 |
| mct_nasa | NASA 센서 요약 계약 | 공개 데이터의 마모 임계값 |
| mct_stiehl | 공통 절삭력 3축·주축 토크 요약 또는 절삭 구간 파형 | 공개 데이터의 마모 임계값 |
| screw_tightening | PyScrew 단계별 토크·각도 그래프 | 실험 조건 이상 |
| screw_equipment_nok | 같은 체결 그래프, 공정 키 명시 | 장비 NOK 라벨 |
| pressfit | 단조 증가 삽입 변위 mm, 동기화된 힘 N | 정상 기준 대비 파형 변화 |

`auto`는 입력 계약이 하나에만 맞을 때 선택합니다. 공장 센서 이름을 임의로 바꾸는 것만으로 모델이 호환되지는 않습니다. 단위, 측정 구간과 특징 정의를 맞춰야 합니다. 입력 단위는 릴리스 `release.json`과 `REPORT_KO.md`를 참고하세요. NASA 요약값은 원본 데이터의 채널 스케일을 유지해야 합니다.

Stiehl 원시 입력은 `phase="cutting"`, `signals={force_x:[...],force_y:[...],force_z:[...],torque_spindle:[...]}`, `units={force_x:"N",force_y:"N",force_z:"N",torque_spindle:"N*m"}`입니다. 각 채널은 같은 절삭 구간을 나타내야 하지만 센서 샘플링 속도가 다르므로 길이는 달라도 됩니다. 기존 Stiehl 요약 함수와 동일하게 계산합니다. `p2p`는 99.5–0.5 백분위 간격입니다.

기존 `Service.predict`도 동일한 상태 폴더에 설치한 활성 파일럿을 사용합니다. 기존 서버 API는 상태를 `AI_STATE_DIR/tenants/<tenant>`에서 읽으므로 그 경로를 설치 스크립트의 `-StateDir`로 지정해야 합니다. 압입 문맥은 기존 predict API의 `meta.context`에 전달합니다. GitHub Pages 정적 웹에는 Python 엔진이 자동 배포되지 않습니다.

## 현장 데이터 수집 및 정상 기준 학습

압입 입력 행에 선택적으로 `engineering_limits`를 지정할 수 있습니다. 예: `{"travel_mm":[7.9,8.1],"peak_force_N":[60,120]}`. 이동 거리, 최대/종단 힘, 힘–변위 적분, 평균 기울기만 지원합니다. `engineering_checks`는 입력된 규격 대비 값이며, 정상 분포 이탈 점수·검사 정답과 구분해 반환합니다. 규격 설정이 바뀌면 프로그램 버전도 변경하세요.

1. 콘솔 또는 `/api/cycle`에 작업 1건씩 입력합니다. 데이터가 잘못되면 오류를 반환하고 저장하지 않습니다.
2. `/api/feedback`에 `event_id`, `outcome`(pass/fail/unknown), `actor`, `evidence`, `note`를 전달합니다. 합격·불합격은 독립 검사 성적서 등 근거가 필수입니다. 모델의 기존 예측을 검사 정답으로 사용하지 마세요.
3. 압입의 같은 조건에서 정상 확인된 40건 이상·3개 LOT 이상을 수집하면 `/api/calibrate`에 `context`를 전달합니다. 학습 ≥20건과 보정 ≥10건을 LOT 단위로 분리합니다. 최초 작업을 자동으로 정상으로 취급하지 않습니다.
4. 이후 작업부터 이상 점수·기준 이탈 여부를 반환합니다. 이때도 불량 확률이나 검출률을 추정하지 않습니다. 이후 별도 LOT와 독립 불량 정답으로 현장 성능을 평가해야 합니다.

최신 검사 정정이 기존 정상 학습 자료를 불합격·미확인으로 바꾸면 그 기준은 추론에서 무효 처리됩니다. 다시 보정해야 합니다. 검사 결과 저장만으로 모델이 자동 학습되지는 않습니다.

모든 POST API는 `Authorization: Bearer <pilot-token>`을 요구하며 요청은 최대 2 MB입니다. 상태 조회는 `POST /api/status`에 `{}`를 보냅니다. 입력 오류 400, 인증 오류 401을 구분합니다. CLI도 동일한 엔진을 사용합니다.

```powershell
cd ai
python -X utf8 -m mlng_ai.pilot status --state state
python -X utf8 -m mlng_ai.pilot submit --state state --input 작업.json --out 결과.json
python -X utf8 -m mlng_ai.pilot feedback --state state --input 검사결과.json
python -X utf8 -m mlng_ai.pilot calibrate --state state --input 조건.json
python -X utf8 -m mlng_ai.pilot train-field --state state --input 학습조건.json
python -X utf8 -m mlng_ai.pilot rollback --state state
```

## 버전 및 해석

`train-field`는 MCT·체결·압입 모두 독립 검사로 pass/fail이 확인된 현장 데이터로 해당 기업 전용 품질 후보를 학습합니다. 학습조건 JSON은 `{"process":"pressfit","context":{"machine_id":"M1","part_number":"P1","program":"v1"}}` 형태입니다. 같은 조건의 검사 완료 작업 100건·5개 LOT 이상이 필요하며, 학습에는 클래스별 10건, 검증·시험에는 각각 클래스별 5건 이상이 있어야 합니다. 이 수치는 실행 최소 조건이며 통계적 신뢰성을 보장하는 표본 수는 아닙니다.

LOT를 기록 순서로 60/20/20 분리하고 전처리는 학습 구간에서만 확정합니다. 검증 결과로 모델과 임계값을 선택한 후 시험을 확인합니다. 검증·시험 모두 AUC ≥0.65, 검출률 ≥50%, 정상 오경보 ≤10%의 사전 파일럿 기준을 통과하면 해당 공정·조건의 현장 모델에 적용하며, 실패하면 보고서와 후보만 남깁니다. 이 기준은 고객 품질 승인 기준과 다르므로 계약 후 별도 합의가 필요합니다. 반복 튜닝 후에는 새로운 시험 LOT를 확보하세요.

현장 후보는 `field-candidates`에 저장되고 기업 전용 SQLite에 등록됩니다. 학습에 사용한 검사 정답이 나중에 바뀌면 해당 후보의 알림을 보류합니다. 다른 회사의 모델이나 공개 기본 모델 가중치에는 반영되지 않습니다. HTTP에서는 학습 명령을 제공하지 않으며 현장 관리자만 CLI에서 실행합니다.

`pilot-releases`의 모델·보고서·입력 예시·체크섬은 릴리스 단위로 남고 활성 포인터만 바뀝니다. 이전 릴리스가 있을 때 rollback으로 모델 전체를 되돌립니다. 현장 SQLite와 정상 기준은 모델 릴리스와 별도로 유지됩니다. 압입 특징 계약이 바뀌면 기존 정상 기준을 그대로 사용하면 안 됩니다.

Stiehl 등 시험 검출률이 10% 미만이면 실험 알림을 보류하고 `alert=null`을 반환합니다. 점수는 연구용으로 남습니다. 10%는 품질 합격 기준이 아니라 낮은 검출률로 정상 판정을 반복하는 것을 막는 최소 표시 규칙입니다. 파일럿 모델은 설비 제어나 자동 품질 합격 권한을 갖지 않습니다.

`REPORT_KO.md`는 각 릴리스의 시험 수치를 설명합니다. 서로 다른 공개 데이터의 수치를 현장 성능이나 하나의 전체 정확도로 합치지 마세요. 신규 Figshare·MATWI 어댑터와 실제 압입 불량 분류 모델은 이 패키지에서 완료했다고 주장하지 않습니다.
