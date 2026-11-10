# 공정별 운영 모델 선택

`Service.predict(process, df, meta)`가 해당 테넌트의 `routes.json`을 먼저 조회합니다. 등록이 없으면 기존 통합 모델 추론을 사용합니다. 등록 경로의 무결성/입력 검증이 실패하면 오류를 반환하며 다른 모델로 조용히 대체하지 않습니다. `/ai/v1/predict`도 같은 경로를 사용합니다.

현재 PC의 `ai/state`에 MCT 3개 공정과 `causalman_pressfit`을 등록했습니다. MCT는 `state/base/mct.json`의 학습된 공통/공정별 파라미터를 선택하고, CausalMan은 다운로드 폴더의 사용자가 학습한 10만 행 실험 결과를 복사했습니다. 등록 시 파일을 전용 폴더로 복사하고 모델/전처리 SHA256을 고정하므로 원본 재학습이 현재 등록 모델에 자동 영향을 주지 않습니다. 이번 등록은 로컬 추론 연결이며 새로운 현장 성능 승인이나 서버 배포가 아닙니다.

## 입력 계약

- `mct_hmotp`, `mct_nasa`, `mct_stiehl`: 모델이 요구하는 전체 특징 슬롯을 이름에 맞춰 전달합니다. 값은 학습 때와 동일한 공구 이력·정규화·sensor/rel variant 처리를 거쳐야 합니다. 정확한 슬롯 이름 검사는 해도 값의 의미까지 자동 확인할 수는 없습니다. 기존 MCT bundle에 원시 센서용 확정 기준이 충분히 저장돼 있지 않아서 raw sensor API는 이 경로에서 지원하지 않습니다. 임의의 0 벡터로 실제 예측하지 마세요.
- `causalman_pressfit`: `preprocessing.json`의 raw_columns와 필요한 LTL/UTL 칼럼을 전달합니다. 학습 시와 같은 규격 대비 변환/정규화/클리핑을 수행합니다. `ProcessResult`, `MpGood` 등의 판정 칼럼은 입력에 포함돼도 특징으로 사용하지 않습니다. 결과는 항상 `synthetic: true`를 표시합니다. 실제 압입 공정을 이 키에 자동 연결하지 않습니다.
- 등록된 전용 모델에 `Service.update`를 호출하면 명시적으로 거부합니다. 별도 후보를 학습하고 검증한 후 재등록해야 합니다. 기존 통합 모델의 후보 승격은 전용 모델 라우팅을 바꾸지 않습니다.
- 상태 조회의 `model_routes`에서 현재 선택 파일·해시·입력 계약을 확인합니다.

## Python 호출

```python
from mlng_ai.service import Service
s = Service('state')
print(s.status()['model_routes'])
# result = s.predict('causalman_pressfit', measurement_dataframe)
```

서버는 `AI_STATE_DIR/tenants/<tenant>/`를 사용합니다. 그 서버에 적용하려면 해당 테넌트의 실제 state 경로에 등록해야 합니다. 이번 PC의 `ai/state` 등록이 서버나 다른 테넌트로 복제되지는 않습니다. 등록/복구 작업은 서버를 정지하거나 관리자 단일 writer로 수행합니다. CLI는 외부 API에 노출하지 않습니다.

```powershell
python -m mlng_ai.routing --state <테넌트상태경로> --process mct_hmotp --kind mct --model state/base/mct.json --approved-by <검증담당자>
```

직전 라우팅 복구는 `Router(state_dir).rollback()`입니다. 통합 모델 버전 복구와 별개이며 마지막 등록 전의 전체 라우팅 표로 복구합니다. 원시 데이터/실험 결과는 공개 Git에 추가하지 말고 테넌트 전용 상태 폴더에서 관리합니다.
