# 공정 템플릿 1차 구현

`mct_endmill.json`, `screw_tightening.json`은 계산·입력 계약입니다. `engineering_limits`는 현장 규격만 넣고, 실험용 정답 기준은 `evaluation_labels.json`으로 분리합니다. 기준이 비어 있으면 합격/불합격을 추정하지 않습니다. 인과적 조건 변경 효과와 자동 설비 제어는 지원하지 않습니다.

```python
from mlng_ai.profiles import load_profile, ProfileRuntime
p = load_profile('mct_endmill')
r = ProfileRuntime(p, tenant='factory_a')
result = r.observe({
    'machine_id':'M1', 'tool_id':'physical_tool_A', 'installation_id':'life_001',
    'material':'steel', 'operation':'finish', 'rpm':1000, 'feed':100, 'depth':1,
    'cut_load':[10,11,12], 'time_seconds':[0,1,2], 'cut_seconds':2,
    'confirmed_normal':True,
})
r.save('/customer-local/state.json')
# 再開: r = ProfileRuntime.load(path, 'factory_a', p)
```

Windows에서는 저장 경로를 해당 고객의 전용 폴더로 지정합니다. 이 모듈의 tenant 검사는 파일 오용 방지용이며 API 인증·접근 제어·동시 쓰기 잠금은 서버 운영 계층에서 적용해야 합니다. `save`는 임시파일 후 교체하며, 한 상태 파일의 writer는 하나여야 합니다.

공구 A→B→A는 A의 상태를 이어갑니다. 실제 공구 교체 시 installation_id를 새로 부여합니다. 조건별 상태와 정상 기준은 분리됩니다. 정상 확인은 해당 시점에 담당자가 승인한 입력이어야 하며 나중에 밝혀진 품질 정답을 운영 입력으로 사용하면 안 됩니다. 기본 10개 승인 표본으로 기준을 확정하고 이후 고정합니다. 현재 작업은 이전 기준으로 먼저 채점하므로 현재 값을 기준에 미리 포함하지 않습니다. 데이터 부족·단계 누락·역전/중복 축은 `available=False`로 반환합니다. 시간·각도 중복은 어댑터에서 합의된 방법으로 정리하고 원본 변경 이력을 보관해야 합니다.

`Table(..., profile=p)`를 `features.encode`에 전달하면 전용 특징이 기존 공통 특징에 추가됩니다. 테이블 경로는 주어진 이력을 처음부터 순차 재생합니다. 온라인 배치 간 이력은 별도 `ProfileRuntime.save/load`로 유지합니다. 기존 Service/API가 자동으로 템플릿을 선택하거나 상태를 불러오는 기능은 아직 없습니다. 호출자가 명시적으로 지정합니다. 템플릿 변경 시 버전·해시를 고정하고 새 특징으로 학습한 후보를 평가한 뒤 기존 운영 승인 절차를 사용합니다.

## 실제 데이터 비교 결과

`python -m mlng_ai.profile_compare <s04.zip 또는 hmotp_cuts.csv> <결과.json>`

- 체결: workpiece 단위 고정 분할. 예외 플래그 제외, 중복 제거, 양 모델에 동일 유효 작업 사용. 학습 대조군으로만 기준 보정. 전체 곡선 평균·표준편차·최댓값과 단계 템플릿 추가 모델 비교. 실험 조건 faulty를 예측하며 최종 결합 불량 검출 결과가 아닙니다.
- MCT: 공구 하나를 빼고 학습하는 3-fold 평가. 초기 10회의 측정 정상 작업으로 각 공구의 기준을 보정하고 평가에서 제외. 원시 곡선을 생성하지 않고 기존 측정 RMS 요약값과 상대 변화 특징을 비교. 공구 3개뿐이라 현장 일반화 성능으로 해석할 수 없습니다.
- 임계값은 각 학습 분할 정상 점수의 95% 분위에서 결정합니다. 시험 데이터의 실제 오경보율이 정확히 5%가 되는 설정은 아닙니다.
- 체결: AUC 0.5955→0.6432, 검출률 21.06%→30.43%, 오경보율 4.43%→4.21% (시험 921회).
- MCT: AUC 0.9486→0.9081, 검출률 69.57%→65.22%, 오경보율 3.64%→4.86% (평가 270회).

이 비교는 신규 계산 특징의 첫 제거 실험입니다. 기존 엔진 전체와 모든 운영 조건을 대표하는 비교가 아닙니다. MCT 추가 특징은 이번 평가에서 악화되어 운영 승격 근거가 없습니다. 체결도 다른 날짜·설비·부품에서 추가 검증해야 합니다. 운영 모델 파일은 교체하지 않았습니다.

회귀 검사: 새 프로필 7개 테스트와 기존 관련 검사 실행. 전체 검사는 기존 `service.py`의 `fcntl` 의존성으로 Windows 수집 실패. 기존 pickle 테스트는 현재 pandas/pyarrow 환경에서 허용 목록과 맞지 않아 실패하며, 이번 변경으로 허용 목록을 넓히지 않았습니다.
