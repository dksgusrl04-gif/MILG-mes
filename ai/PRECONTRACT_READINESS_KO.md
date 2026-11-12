# MILG · 고객 계약 전 배포 및 발표 준비

자동 학습 운영은 `AUTOMATIC_LEARNING_KO.md`를 따른다. 최신 배포 시작 스크립트는 별도 학습 점검 프로세스를 실행한다. 운영 화면은 `/operations`이며, 설치 후 대상 공정·설비·품목·프로그램을 명시적으로 활성화하고 작업·독립 검사·LOT 종료를 연결한다. 데이터 API 토큰과 학습 설정·복구 관리자 토큰은 분리한다. 공개 MES 예시는 기업별 학습에 들어가지 않는다.

목표는 고객 데이터 연결을 시작할 수 있는 제품입니다. 공정별 품질 보장, 고객 ROI, 장기 가동을 완료했다고 표시하지 않습니다. 등록 모델 수와 지원 공정 수는 다릅니다. 같은 공정의 데이터 출처별 모델이 여러 개 등록돼 있습니다.

## 배포 패키지 사용

ZIP을 새 폴더에 풀고 Python을 설치한 뒤 PowerShell에서 실행합니다. 패키지의 `delivery-manifest.json`에 기록된 Python 및 라이브러리 버전을 사용합니다. Python 자체는 포함하지 않습니다. `offline_dependencies_bundled:true`인 패키지는 동일한 운영체제·CPU 아키텍처·Python용 의존성 wheel을 포함합니다. 그 외 패키지의 최초 의존성 설치에는 인터넷 또는 내부 패키지 저장소가 필요합니다. 추론에는 외부 AI API가 필요하지 않습니다. 이것이 전체 고객 보안 심사나 폐쇄망 운영 검증 완료를 뜻하지는 않습니다.

```powershell
.\Start-MilgDelivery.ps1 -InstallDependencies
# wheels 폴더가 있는 오프라인 패키지: Python은 사전 설치
.\Start-MilgDelivery.ps1 -InstallDependencies -Offline
# 다음 실행
.\Start-MilgDelivery.ps1
```

서버가 실행되면 **`http://127.0.0.1:8765/mes/`에서 기존 MES 안의 위젯으로 대표 시연**을 진행합니다. 우측 상단 ‘엔진 연결’에 토큰과 담당자를 입력합니다. API 토큰은 패키지의 `runtime-state/pilot-token.txt`에 처음 실행 시 새로 만들어집니다. 공개 웹페이지에 토큰을 넣지 않습니다. 서버가 이미 실행 중인 포트는 사용하지 않습니다. 상세 입력·검사 결과 관리는 `/`, 독립 점검 화면은 `/demo`입니다.

서버 종료 후 재실행해도 작업·담당자 조사는 유지됩니다. 모델을 바꾸려면 승인된 새 release를 설치한 뒤 서버를 재시작합니다. 실행 중인 서버는 시작 시 읽은 release를 사용합니다.

## 대표 발표 시연 · 4분

1. 공개 데이터 기반 시연임을 알립니다. 고객 도입 성과로 설명하지 않습니다.
2. `/mes/`에서 토큰을 입력하고 연결합니다. 공정 실적 → MCT 가공 → 작업별 AI에서 공구/LOT를 고릅니다. 작업을 선택하고 우측 하단 MILG 버튼 또는 행의 ‘MILG 분석’을 누릅니다.
3. 선택한 MES 작업의 센서 6개 요약값만 실제 `mct_nasa` 모델에 전송합니다. 마모 정답·기존 위험도는 입력하지 않습니다. 위젯에서 점수와 근거를 보고 조사 등록 또는 보류합니다. 이는 독립 검사 합불·설비 정지가 아닙니다.
4. 품질 → MILG 조사 이력에서 서버에 저장된 담당자 처리 기록을 확인합니다. 새로고침해도 기록은 유지되지만 토큰은 다시 입력해야 합니다. 작업 변경 시 이전 근거를 지우며 미연결 공정/LOT에는 임의 모델이나 다른 예시를 적용하지 않습니다.
5. `verification/acceptance.html`을 보여주며 모델 호출, 재시작, 중복 전송, 기업별 저장소 분리, 백업·복구 시험 결과를 설명합니다. 이 표는 예측 정확도 검증표가 아닙니다. 누락 센서/네트워크 오류 시험은 별도 `/demo`나 테스트 스크립트로 확인합니다.

MES의 날짜/LOT 번호는 발표 시점을 기준으로 이동된 공개 기록입니다. 위젯은 현재 등록 모델로 실제 계산하지만, 이 기록을 미사용 신규 시험으로 해석하지 않습니다. 기존 MCT 현황/공구 상세의 곡선·위험도는 저장된 평가 기록이며 실시간 위젯 결과와 다를 수 있습니다. 대표 시연 화면 ‘작업별 AI’는 현재 선택한 측정값과 실제 호출 결과를 사용합니다. 다른 공정 모델이 등록돼 있어도 해당 MES 센서 매핑까지 모두 연결됐다는 뜻은 아닙니다.

로컬 MES 시연은 외부 데이터/API를 연결하지 않으며 별도 위젯만 표시합니다. 기존 공개 GitHub Pages 화면에는 로컬 토큰을 넣거나 PC의 엔진 연결을 자동 공개하지 않습니다.

시연 서버는 한 기업용 프로세스입니다. 기업별 별도 상태 디렉터리·프로세스·토큰·접근 경계를 사용합니다. 중앙 다중 기업 서비스의 SSO/RBAC·침투시험·고가용성을 갖췄다고 설명하지 않습니다. 외부 제공 시 HTTPS 역방향 프록시와 고객 인증 설계가 필요합니다.

## CSV 연결

CSV는 완료 작업 한 건당 한 행, 명시적인 칼럼 매핑·단위·템플릿을 사용합니다. 원시 파형을 임의로 요약하지 않습니다. `python -m mlng_ai.connector template --state state --process wld_rsw_scenario --out reports/connector-example`로 예시를 생성합니다. 실제 센서 단위·프로그램·LOT·작업 ID로 매핑을 확인한 뒤 먼저 `validate`하고 그다음 `send`합니다. 예시·발표 데이터의 `demo`는 true로 유지합니다. 실제 데이터만 명시적으로 false로 설정합니다.

```powershell
python -X utf8 -m mlng_ai.connector template --state state --process wld_rsw_scenario --out reports/connector-example
python -X utf8 -m mlng_ai.connector validate --state state --csv reports/connector-example/cycles.csv --mapping reports/connector-example/mapping.json --out reports/connector-example/validation.json
python -X utf8 -m mlng_ai.connector send --state state --csv reports/connector-example/cycles.csv --mapping reports/connector-example/mapping.json --out reports/connector-example/submission.json
```

`UNCONFIRMED`는 단위 미확인 표시입니다. 발표 예시에만 허용하며 `demo:false`인 실측 입력에서는 반드시 확인한 단위로 바꿔야 합니다. `source_unit`, `engine_unit`, `scale`, `offset`을 명시하며 차원이 다른 센서를 단위 변환으로 맞추면 안 됩니다. 코드가 실제 센서 교정이나 단위의 진위를 대신 확인해 주지는 않습니다.

CSV 전체의 필수 칼럼·유한 값·시간대 포함 시각·시간순·중복 작업 ID·기존 작업 충돌을 검사한 뒤 전송합니다. 전송 도중 장애가 나면 일부 작업은 저장됐을 수 있습니다. 같은 파일과 매핑으로 재실행하면 동일 입력 작업은 중복 저장하지 않습니다. 파일이나 단위를 수정한 경우에는 새 작업 ID를 임의로 만들어 덮지 말고 원본 이력과 변경 이유를 확인합니다. 숫자 CSV를 받는 모듈이며 로봇 곡선 등 중첩 입력은 기존 JSON API를 사용합니다.

## 운영 검증 · 원본 저장소를 바꾸지 않음

ai 폴더에서 실행합니다. 배포 패키지에서는 `--state ../runtime-state`를 사용합니다.

```powershell
python -X utf8 -m mlng_ai.delivery check --state state --out reports/delivery-check
python -X utf8 -m mlng_ai.delivery backup --state state --out reports/private-backup-001
python -X utf8 -m mlng_ai.delivery restore --source reports/private-backup-001 --state reports/restored-state-001
python -X utf8 -m mlng_ai.pilot serve --state reports/restored-state-001 --port 8766
```

백업에는 작업·검사·조사·기업별 학습 모델이 들어가므로 고객의 보안 저장소에 보관합니다. 운영 토큰은 복사하지 않습니다. 복구는 **새 상태 폴더에만** 허용합니다. 기존 상태를 덮어쓰지 않으며, 복구본을 확인한 뒤 시작 스크립트의 상태 폴더를 변경합니다. 깨진 백업은 체크섬 검사에서 거부합니다. 배포 ZIP은 백업과 다르며 운영 DB·토큰·기업별 학습 모델을 넣지 않습니다.

체크섬은 변조/손상을 발견하는 수단이며 배포자의 신원 인증은 아닙니다. joblib은 신뢰할 수 있는 내부 산출물만 읽습니다. 임의 업로드 모델을 실행하지 않습니다.

## 기능 동결과 남은 일

발표 전에는 새 공정 추가보다 대표 입력→분석→근거→조사 기록을 고정합니다. 참고 분석, 기준 이탈, 실제 검사 합불을 구분합니다. 공개 데이터 수치는 데이터 출처·시험 묶음·검출률·정상 오경보를 함께 제시합니다. 효과가 약한 후보를 억지로 등록하지 않습니다.

고객 없이 검증하는 범위: 설치 패키지 무결성, 공개 예시 호출, 모델별 입력 규격, 누락·오류 처리, 중복 작업 충돌, 재시작·백업·복구, 기업별 상태 분리, 위젯 연동.

고객이 필요한 범위: 실제 센서 단위와 작업 경계 승인, LOT/검사 정답 연결, 현장 임계값 보정, 알림 업무량, 새 기간/설비 성능, 실제 가동 안정성, 조사 시간 감소와 비용 효과, 고객 보안 심사. 로컬 검증을 통과해도 이 항목은 완료로 바뀌지 않습니다.

## 첫 상품의 고정 범위

한 공정·한 설비군·한 입력 규격의 작업 후 분석을 대상으로 합니다. 납품물은 칼럼 매핑, 선택 템플릿/모델 버전, 입력 진단표, 위젯, 조사 기록, 평가 보고서, 설치/복구 설명서입니다. 기본 계약에서 MES 전체 교체·설비 자동 제어·무제한 맞춤 개발을 약속하지 않습니다.

연동 원가는 매핑·현장 확인·배포·지원 시간을 기록해 계산합니다. 검증 성공 기준(기존 규칙 대비 성능, 일일 허용 알림, 조사 시간)을 고객과 사전에 정합니다. ROI·예방 불량 수·도입 기간·가격은 아직 확인하지 않은 수치를 실적으로 제시하지 않습니다.
