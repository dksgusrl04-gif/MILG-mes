# MLNG 공정 통합 AI (`mlng_ai`)

공정마다 다른 칼럼을 **이름 · 설명 · 단위 · 값 모양으로 스스로 해석**해, 여러 공정을 **한 모델이 누적 학습**하고 **공정 간 원인**까지 거슬러 찾는 모델.

```
칼럼 해석 (schema)  →  의미 슬롯 (features)  →  LOT 추적으로 앞 공정 연결  →  누적 베이즈 학습 (model)  →  검증 (engine)
 이름·단위·값 모양      «온도가 평소보다 몇 σ»     up: 슬롯                     공통 효과 + 공정별 보정          시간순 · 처음 보는 공정 · 정답 주입
```

| 단계 | 하는 일 | 파일 |
|---|---|---|
| 칼럼 의미 해석 | 역할(조건 · 결과 · 결과 세부 · 수량 · 범주 · 식별자 · 시각 · 설정값)과 물리량(온도 · 압력 · 전류 … 18종)을 제조 용어 사전 + 문자 n-그램 임베딩 + 단위 + 값 분포로 판단. 결과의 «세부 유형»(고장 모드)과 결과와 거의 같은 칼럼은 **누설로 자동 제외**. 애매한 칼럼만 (선택) Claude 에 물어보고, 사람이 확정한 값이 최우선 | `schema.py` `lexicon.py` |
| 공정 공통 표현 | 칼럼이 달라도 같은 자리: 물리량별 이탈 · 변동 증가 · 규격 이탈 · 사건 · 드문 범주 · 수량 과다 · 앞 공정(up:). «평소» 는 그 행 **이전까지** 쌓인 값으로만 (누적 · 미래 정보 없음) | `features.py` |
| 누적 학습 | log(기대 불량) = log(수량) + α공정 + Σ(β공통 + δ공정)·x. 배치마다 사후분포 갱신(라플라스), 망각 계수 · 입력 변화(PSI) 감지 시 빠른 적응, 공정 기준율 천천히 이동(상태 공간), 과산포 보정, **신뢰 게이트**(기준율보다 못하면 기준율로 물러남) | `model.py` |
| 검증 | ① 시간순 «예측 → 채점 → 학습» ② 처음 보는 공정: 다른 공정으로 미리 배운 모델 vs 처음부터 ③ 공정 간 원인: 정답을 심은 가상 데이터 ④ 칼럼 해석: 처음 보는 이름 72개 | `engine.py` `holdout.py` |

## 실행 (ai/ 폴더에서)

```
pip install -r requirements.txt
python -m mlng_ai fetch          # 공개 데이터(UCI: AI4I 2020 · Cylinder Bands · SECOM) 받기
python -m mlng_ai train          # 학습 · 검증 → state/model.json · ../web/data/ai_model.json · reports/report.md (약 1분)
python -m mlng_ai map 파일.csv    # 처음 보는 CSV 칼럼 해석 (--llm: 애매한 칼럼만 Claude)
python -m pytest -q              # 시험
```

결과는 [reports/report.md](reports/report.md). MES 화면: 더보기 › 분석 › **공정 통합 AI**.
서버(server/)에서는 `/ai/v1/map` (칼럼 해석) · `/ai/v1/update` (새 데이터로 누적 학습 1회) · `/ai/v1/status`.

## 데이터
- MLNG 데모 공장 5개 공정 (`web/data/demo.json`): 열처리 · 프레스 · 점용접 · MCT 가공 · 정밀가공 검사
- 공개 3개 (UCI, CC BY 4.0): AI4I 2020 예지보전 · Cylinder Bands 인쇄 불량 · SECOM 반도체 (칼럼 이름 없음)
- 협력사 데이터는 쓰지 않는다
