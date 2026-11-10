# CRIA 독립 학습·평가

```powershell
cd "C:\Users\dksgu\Desktop\code\mlng-mes\mlng-mes\ai"
python -m pip install -r requirements-cria.txt
powershell -NoProfile -ExecutionPolicy Bypass -File "C:\Users\dksgu\Desktop\code\mlng-mes\mlng-mes\tools\Train-CriaInsertion.ps1"
```

결과는 CRIA_insert_dataset 아래 `_milg/cria_candidates/<실행시각>/`에 저장합니다. 원본 데이터·MILG 운영 가중치·모델 등록은 변경하지 않습니다.

- `episode_failure`: 작업 종료 후 전체 측정 곡선의 요약으로 실패 여부 분류. 조기 경보나 최종 결합력 검증이 아닙니다.
- `frame_anomaly`: 현재 제공된 힘 청크와 이전 프레임의 측정값으로 해당 프레임의 이상 주석 탐지. 미래 프레임은 사용하지 않습니다. 실제 실시간 적용 전 센서 청크와 제어 시각의 정확한 정렬 의미를 확인해야 합니다. 미래 이상을 예측하는 모델은 아닙니다.
- 각 목표에 LogisticRegression, HistGradientBoostingClassifier를 별도 학습합니다. 원시 곡선을 이용해 특징을 만들지만 딥러닝 시계열 모델은 아닙니다.
- 목표별 작업 라벨/프레임 라벨을 정답으로만 사용. ID, source_id, 시각, 명령, interaction_label, 작업 길이는 입력에서 제외합니다.
- 기본 분할: UTC 수집 날짜 단위 순서대로 학습/검증/시험 배정. 같은 에피소드·수집 날짜가 양쪽에 들어가지 않습니다. 날짜 수 기준 60/20/20이며 작업 개수 비율은 다릅니다. 시간에 따른 조작 주체/조건 분포 변화가 결과에 영향을 줄 수 있습니다.
- 전처리는 학습 구간에서만 추정. 검증 정상 데이터의 분위수로 목표 오경보율을 설정하고, 검증 성능으로 후보를 선택합니다. 시험 오경보가 같은 수치가 될 것이라는 보장은 없습니다.
- 프레임 학습은 작업마다 시간축에서 균등하게 최대 120개를 추출하고 작업별 가중치를 맞춥니다. 검증·시험은 모든 프레임을 평가합니다. 연속 프레임은 독립 표본이 아니므로 프레임 수를 독립 작업 수로 해석하면 안 됩니다.
- 첫 프레임의 120Hz 힘 청크는 왼쪽 반복 패딩 형태를 확인하고 뒤쪽 valid_len개만 사용합니다. 알 수 없는 패딩 형태는 오류로 중단합니다.

추가 일반화 시험:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "C:\Users\dksgu\Desktop\code\mlng-mes\mlng-mes\tools\Train-CriaInsertion.ps1" -HoldoutTask insert_typec
```

위 옵션은 Type-C 작업 전체를 시험, 정렬상 마지막 다른 작업을 검증, 나머지 작업을 학습으로 사용합니다. 날짜 제외 시험과는 다른 실험입니다. 시험 결과를 보고 유리한 분할을 반복 선택하지 마세요.

출력: `report.md`, `report.json`, 입력 파일 SHA256·분할 목록을 담은 `manifest.json`, 목표별 후보 모델 4개의 `.joblib`, 시험 예측 CSV. `.joblib`은 전처리 설정과 임계값을 포함합니다. 자신이 생성한 파일만 로드하세요. 작업 종류별 시험 결과는 JSON의 by_task에 기록합니다. 이 파일들은 현재 MCT/CausalMan 라우터에 자동 등록되지 않습니다.

CRIA 출처: https://huggingface.co/datasets/Joringell/CRIA (CC BY 4.0). 로컬 삽입 4종 600작업으로 평가하며, 산업용 압입 결합 품질이나 다른 설비의 성능을 입증하지 않습니다.
