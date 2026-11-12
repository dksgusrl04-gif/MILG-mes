# MILG Engine — 엔진 하나 + 공정별 시나리오 템플릿

공정이 바뀌어도 엔진 코드는 고치지 않습니다. 공정마다 다른 것은 `scenarios/*.yaml` 한 장뿐입니다.

```
milg_engine/
├─ engine.py                  # 공통 엔진 (모든 공정이 같은 7단계를 탐)
├─ scenarios/
│  ├─ _template.yaml          # 새 공정 시나리오를 쓸 때 복사해서 시작
│  ├─ inj_cycle_passfail.yaml # 사출  INJ-01 샷 양불
│  ├─ inj_quality_grade.yaml  # 사출  INJ-02 품질 등급
│  ├─ press_insertion_offset.yaml  # 압입 PRS-01 정렬 불량
│  ├─ ht_lot_defect.yaml      # 열처리 HT-01 로트 위험군
│  └─ mct_tool_wear.yaml      # MCT   MCT-01 공구 마모
└─ outputs/<시나리오ID>/      # report.md · report.json · predictions.csv
```

## 실행

```bash
pip install pandas numpy scikit-learn pyyaml openpyxl
python engine.py scenarios/*.yaml
python engine.py scenarios/mct_tool_wear.yaml --max-seconds 150   # 파일이 많으면 나눠서, 캐시로 이어감
```

## 엔진의 7단계와 템플릿 항목

| 단계 | 템플릿 키 | 공정마다 바뀌는 것 |
|---|---|---|
| 1. 판정 단위 | `unit` | `table`(행=샷) · `grouped_table`(초 단위 → 로트 집계) · `files`(파일=삽입·절삭 1회) |
| 2. 정답 | `task`, `label` | 컬럼 / 경로 이름 / 라벨표 조인 / 라벨표 행 번호, 이진·다중·회귀 |
| 3. 특성 | `features`, `derive` | 제외할 식별자·누수 컬럼, 파생 컬럼 |
| 4. 평가 분할 | `split` | `random` · `group`(설비·셋업·공구·작업일) · `time` |
| 5. 모델 | `model` | 후보 목록 (baseline은 항상 같이 비교) |
| 6. 판정 규칙 | `decision` | 불량 재현율 목표 임계값 / 관리 한계(마모 등) |
| 7. MES 출력 | `output` | 같이 실을 컬럼 |

시나리오는 결국 네 가지 질문에 답하는 문서입니다.

1. **무엇을 한 건으로 볼 것인가** — 샷, 로트, 삽입 1회, 절삭 1회
2. **정답은 어디에 있는가** — 같은 행, 폴더 이름, 별도 품질표
3. **현장에 나갔을 때와 같은 조건으로 평가하려면 어떻게 나눌 것인가** — 처음 보는 날·셋업·공구
4. **결과를 어떤 판정으로 바꿀 것인가** — OK / WARN / NG(STOP)

## MES 공통 출력 (`predictions.csv`)

공정과 무관하게 컬럼이 같아서 MES 쪽 연동은 한 번만 만들면 됩니다.

| 컬럼 | 뜻 |
|---|---|
| scenario, process | 시나리오 ID, 공정 |
| unit_id | 샷 ID · 배정번호 · 파일 경로 |
| split | train / test |
| y_true, y_pred, score | 정답, 예측, 불량 확률(이진) |
| decision | OK · WARN · NG · STOP |
| model, engine_version | 추적용 |
| (carry_columns) | 시나리오에서 지정한 컬럼 |

## 새 공정 추가 순서

1. `_template.yaml`을 복사해서 이름을 바꿉니다.
2. `unit` 유형 셋 중 하나를 고릅니다.
3. 정답 위치를 `label`에 적고, 판정 이후에 기록되는 컬럼(불량 사유 등)은 `features.exclude`에 넣습니다.
4. `split`을 현장 배포 조건에 맞춥니다. 같은 실험 조건의 반복 사이클이 학습·평가에 나뉘면 성능이 부풀려집니다.
5. 실행 후 `outputs/<ID>/report.md`에서 baseline 대비 개선 여부를 확인합니다.

## 선행연구를 반영한 옵션 (2026-11-09 실험 후 추가)

| 옵션 | 근거 연구 | 쓰는 곳 | 실험 결과 | 기본값 |
|---|---|---|---|---|
| `audit` (데이터 수집 진단) | DMME (Wiemer 등 2019, CRISP-DM에 수집 단계 추가) | 모든 시나리오 | MCT 수집 오류(ExecPtRet −7), 블록 번호 고정, 정수 기록 신호를 자동으로 찾음 | 항상 실행 |
| `unit.cells` + `anomaly.cell_baseline` (위치 셀 비교) | Netzer 등 2021 (이송축 위치로 가공 구간 분할) | `segments` 단위 + `anomaly` | 같은 오경보 수준에서 전류 ×1.2 검출 8.8% → 69.7% | MCT-02에서 사용 |
| `anomaly.threshold_scope: context` (하위 공정별 임계값) | KIT 2022 (subprocess-specific thresholds) | `anomaly` | 검출은 오르지만 오경보도 함께 늘어남(NG 0.7% → 2.0%) | `global` |
| `model.search` (하이퍼파라미터 탐색) | 제조 품질 AutoML (APMS 2022, PREFERML) | 지도학습 | INJ-01 정밀도 0.5→1.0, MCT-01은 오히려 나빠짐(과적합) | 끔 |

```yaml
# 예: 위치 셀 비교
unit:
  type: segments
  cells: {columns: [X_Mac, Y_Mac, Z_Mac, B_AxisPos], step: 5, channels: [Cur_R_A, ...]}
anomaly:
  cell_baseline: {channels: [Cur_R_A, ...], min_train: 5}
  sensitivity:
    - {name: 전류, columns: ["Cur_*_A__*"], cell_channels: [Cur_R_A, Cur_S_A, Cur_T_A], factors: [1.1, 1.2]}

# 예: 하이퍼파라미터 탐색 (평가 그룹이 적으면 과적합 위험)
model: {candidates: [logistic, random_forest, hgb], search: {n_iter: 12}}
```

실험 코드: `experiments/mct_methods.py`(MCT 방법 비교), `experiments/automl_compare_v2.py`(AutoML 비교)

## 추가 옵션 (INJ-01 중복 점검 후)

| 옵션 | 하는 일 | 쓰는 곳 |
|---|---|---|
| `unit.collapse` (+ `pre_filters`) | 완전 중복 행 제거 후 키(예: TimeStamp)별로 1개 단위로 합침. `any`에 적은 값이 하나라도 있으면 그 값(예: LH·RH 중 하나라도 불량이면 불량 샷) | INJ-01, INJ-03 |
| `features.trailing_baseline` | 같은 묶음(작업일) 안 직전 N개 중앙값 대비 편차만 남겨 일간 조건 변화를 흡수 | INJ-03 |
| `anomaly.label_eval` | 라벨이 있으면 기준 구간 불량을 빼고 양품만으로 정상 범위를 잡은 뒤, 평가 구간 실제 불량 검출률·오경보율을 리포트 | INJ-03 |

INJ-01 점검 결과: 원본 6,736행은 실제 1,995샷(2캐비티 LH·RH + 같은 _id 이중 기록), 불량 샷은 39개가 아니라 20개.
불량이 있는 3일을 번갈아 평가하면 지도학습은 재현율 40%·오경보 54%로 일반화되지 않음(`experiments/inj_day_holdout.py`).
→ INJ-03(양품 기준 이상 탐지 + 같은 날 직전 샷 기준): 가동 초기 불량 5/5 검출, 가스·미성형 3/15, 양품 오경보 NG 0%·WARN 3.7%.

## 시나리오 검증 (엔진 0.2.0)

시나리오 YAML은 실행 전에 자동으로 검사합니다. 오류가 하나라도 있으면 실행하지 않고, 모든 오류를 한 번에 보여줍니다.

```bash
python engine.py --check "scenarios/*.yaml"     # 실행하지 않고 파일만 검사
```

| 단계 | 시점 | 잡는 것 | 예시 메시지 |
|---|---|---|---|
| 1. 파일 | YAML을 읽자마자 | 문법, 모르는 키(오타), 빠진 필수 키, 형식·범위, 허용 값 | `split.tset_size: 모르는 키입니다 (혹시 test_size?)` |
| 2. 조합 | 〃 | 서로 맞지 않는 설정 (task와 decision, unit 유형과 키, group 분할인데 group 없음 등) | `decision.type: limit은 regression에서만 쓸 수 있습니다` |
| 3. 데이터 | 데이터를 읽은 뒤 | 가리키는 컬럼·평가 그룹이 실제로 있는지 | `split.group: 컬럼 'work_dat'이 단위 표에 없습니다 (혹시 work_date?)` |

- 오류는 실행을 막고, 경고는 실행은 하되 알려줍니다(예: random 분할, 데이터에 없는 제외 컬럼).
- 스키마는 `schema.py` 한 파일에 있습니다. 엔진에 새 옵션을 넣으면 여기에도 키를 추가해야 합니다.
- 외부 패키지가 필요 없습니다.

## 데이터 형식 확장 (체결·프레스·용접 추가, 엔진 0.2.x)

| 옵션 | 하는 일 | 쓰는 곳 |
|---|---|---|
| `unit.reader` + `adapters.py` | 장비 전용 파일 형식을 읽는 플러그인. 새 장비는 읽는 함수 하나만 추가 | FAS (체결기 JSON) |
| `unit.segment_column` | 의미 있는 단계별로 따로 요약 (찾기·나사 성형·예비 체결·본 체결) | FAS |
| `unit.attach` | 다른 표(라벨·시각·기존 판정)를 단위에 붙임 | FAS |
| `unit.merge` + `wide_series` | 키로 나뉜 여러 파일 합치기, 한 행에 펼쳐진 파형(T-0…T-999)을 실제 길이 기준 통계로 | WLD-01, WLD-03 |
| `unit.type: windows` | 사이클 표시가 없는 연속 스트림을 고정 길이 창으로 | PRF-01 |
| `derive … fn: concat` | 여러 컬럼을 이어 한 키로 (예: 용접 조건) | WLD-02 |
| `anomaly.label_eval.compare` | 기존 판정(예: 체결기 OK/NOK)과 같은 평가 구간에서 나란히 비교 | FAS-01 |

고친 문제: 같은 키가 반복되는 파일 합치기(n번째끼리 짝), 학습 구간에 불량이 없는 폴드는 임계값 결정에서 제외, 아주 작은 확률을 반올림해 임계값이 0이 되던 문제, 파일형 단위 중간 저장(300개마다).
주의: YAML에서 `on`·`yes`·`no` 같은 키는 참/거짓으로 읽힌다. 스키마 검증이 알려준다.

## MILG 판정 엔진 연결 (엔진 0.3.0 · 2026-11-12)

시나리오 엔진(이 폴더)과 MILG 판정 엔진(`../mlng_ai` · 누적 베이지안 모델)을 합쳤다. 틀은 시나리오, 판정 후보에 `milg` 가 하나 더 생긴다.

| 바뀐 것 | 내용 |
|---|---|
| `model.candidates` 에 `milg` | logistic · random_forest · hgb 와 같은 분할 · 같은 지표로 비교 (이진 판정 전용). `milg_core.py` 가 연결한다 |
| `report` 의 «후보별 평가 구간 비교» | 뽑힌 후보만이 아니라 후보 전부의 평가 구간 ROC-AUC · PR-AUC (보고용 — 선택은 학습 구간 교차검증 그대로) |
| `predictions.csv` 의 `cause_candidates` | `milg` 가 뽑히면 WARN · NG 건마다 원인 후보(불량 쪽으로 민 특성) |
| 실행 | `python -m mlng_ai scenario scenarios/inj_cycle_passfail.yaml` (기존 `python engine.py …` 도 그대로) |

같은 데이터 · 같은 분할 결과 (평가 구간 ROC-AUC, 굵게 = 교차검증으로 뽑힌 후보)

| 시나리오 | 평가 단위 · 불량 | logistic | random_forest | hgb | milg |
|---|---|---|---|---|---|
| INJ-01 사출 샷 양불 (처음 보는 작업일) | 314 · 5 | **0.77** | 0.61 | 0.63 | 0.89 |
| WLD-02 점용접 품질 (처음 보는 조건) | 76 · 11 | 0.63 | 0.82 | 0.84 | **0.83** |
| WLD-03 차체 점용접 (시간순 · 연구용) | 653 · 43 | 0.72 | 0.87 | **0.83** | 0.79 |
| HT-01 열처리 로트 위험군 (시간순) | 41 · 7 | 0.40 | **0.52** | 0.45 | 0.50 |

- 설비 로그에서 나온 실험 결과 파일(`experiments/mct_methods_result.json` · `mct_realistic.json` · `toolchange.json`)은 공개 저장소에 올리지 않는다.

## 시나리오 배포 연결 (0.4.0)

시나리오 실행은 이제 `candidate.joblib`, `input-contract.json`, `example-features.json`도 저장합니다. 모델, 학습된 전처리/정상 기준, 임계값, 템플릿, 평가 결과, 학습 환경 정보를 함께 보관합니다.

- 이상 탐지의 원래 시험 구간은 고정합니다. 불량 제거는 정상 기준 구간에서만 수행합니다.
- 정상 기준 학습과 임계값 보정도 분리합니다. 센서 누락은 `UNKNOWN`이며 0점/정상으로 바꾸지 않습니다.
- 공구가 2개여도 GroupKFold를 유지합니다. 시간 검증은 타임스탬프가 엄격히 분리되며 같은 시각의 기록이 양쪽에 섞이지 않습니다.
- 이진 분류 임계값은 훈련 OOF 예측에서 정상 오경보 예산(기본 10%)을 지키면서 선택합니다. 목표 검출률을 달성하지 못하면 그 사실이 보고서에 남으며 등록 기준도 별도 검사합니다.
- 분류 등록은 시험 AUC 0.75 이상·검출률 50% 이상·정상 오경보 10% 이하, 최소 불량 5개·정상 20개와 검증 구간 기준을 확인합니다. 회귀/다중 분류/정상 기준 전용 모델은 목적에 맞는 별도 기준을 적용합니다.
- 비상업 연구 자료는 운영 등록에서 제외합니다. 등록에는 확인한 원출처 이용 조건을 명시해야 합니다.

```powershell
# ai 폴더에서 실행. 데이터 위치는 자신의 실제 경로로 지정합니다.
python -X utf8 -m mlng_ai scenario milg_engine/scenarios/wld_rsw_quality.yaml `
  --data-root "데이터를 푼 실제 루트" `
  --out reports/scenario-run `
  --install-state state --process wld_rsw_scenario `
  --source-license CC-BY-4.0 --approved-by 담당자명
```

`--data-root` 아래의 파일 배치는 YAML의 `unit.path`, `label.path`를 따릅니다. 기존 ZIP/CSV를 임의로 이동할 필요 없이 경로만 맞춘 시나리오 사본을 사용해도 됩니다. 기준 미달이면 기존 활성 모델은 유지합니다. 등록된 모델은 기존 로컬 파일럿 API `/api/cycle`과 `milg-ai-widget`에서 호출됩니다.

**실시간 입력 경계:** 현재 계약은 `completed_unit_features`입니다. 작업 1회가 완료된 후 시나리오와 동일한 단위 집계·구간 요약·파생 계산을 거친 특성을 전송합니다. 원시 파일 경로나 임의 파일을 API로 보내 읽게 하지 않습니다. `input-contract.json`의 칼럼과 `example-features.json`을 기준으로 MES/수집 에이전트의 입력을 맞춥니다. `trailing_baseline` 등 이력 계산이 있는 템플릿은 그 계산까지 적용된 특성을 공급해야 하며, 원시 센서값으로 대체하면 안 됩니다.

등록 후에도 `alert`와 `quality_prediction`은 현장 승인 전에는 비워 둡니다. `benchmark_decision`과 근거를 검토할 수 있으나 설비 정지·자동 출하 승인을 실행하지 않습니다. 배포 환경에는 `training_environment`에 기록한 라이브러리 버전을 맞춥니다. 이 PC의 모델 등록은 GitHub Pages에 Python 서버를 공개하는 작업이 아닙니다.
