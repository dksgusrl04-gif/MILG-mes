# 필요한 데이터 위치

이 압축 파일에는 데이터가 없습니다. 시나리오는 `milg_engine` 폴더의 **한 단계 위 폴더**(`data_root: ../..`)를 데이터 루트로 봅니다.
아래 경로대로 데이터를 두면 그대로 실행됩니다. 경로가 다르면 각 시나리오의 `data_root` 또는 경로만 고치면 됩니다.

```
milg/
├─ milg_engine/   ← 이 압축 파일
├─ 사출/  압입/  열처리/  MCT/  체결/  프레스/  용접/   ← 데이터
```

| 시나리오 | 공정 | 데이터 경로 (milg 기준) | 비고 |
|---|---|---|---|
| FAS-01 | 체결 | `체결/체결-20261111T065034Z-1-003/체결/s04_variations-in-assembly-conditions-2/json`<br>`체결/체결-20261111T065034Z-1-003/체결/s04_variations-in-assembly-conditions-2/labels.csv` |  |
| FAS-02 | 체결 | `체결/체결-20261111T065034Z-1-003/체결/s04_variations-in-assembly-conditions-2/json`<br>`체결/체결-20261111T065034Z-1-003/체결/s04_variations-in-assembly-conditions-2/labels.csv` |  |
| HT-01 | 열처리 | `열처리/열처리-20261109T041514Z-1-001/열처리/Dataset_열처리 품질보증 AI 데이터셋/data/data.csv`<br>`열처리/열처리-20261109T041514Z-1-001/열처리/Dataset_열처리 품질보증 AI 데이터셋/data/열처리_품질데이터.xlsx` |  |
| INJ-01 | 사출 | `사출/04. Dataset_Molding/dataset/labeled_data.csv` |  |
| INJ-02 | 사출 | `사출/Polenta 외·AIRTLab, iGuzzini 생산 데이터_data.csv` |  |
| INJ-03 | 사출 | `사출/04. Dataset_Molding/dataset/labeled_data.csv` |  |
| MCT-02 | MCT | `MCT/DBexport_*.csv` |  |
| MCT-01 | MCT | `MCT/HMoTP 2024-20261012T030348Z-1-001 (1)/HMoTP 2024`<br>`MCT/HMoTP 2024-20261012T030348Z-1-001 (1)/HMoTP 2024/ToolWear{tool}.csv` |  |
| PRS-01 | 압입 | `압입/insertion-dataset/kd-feb4` |  |
| PRF-01 | 프레스 | `프레스/_extracted/MILG_04_Press_Forming/*_data*.csv` |  |
| WLD-02 | 용접 | `용접/_extracted/MILG_03_Spot_Welding/01_CC_BY_4_0_RSW_Insights/Data_RSW.csv` |  |
| WLD-01 | 용접 | `용접/_extracted/MILG_03_Spot_Welding/03_RESEARCH_ONLY_CC_BY_NC_4_0_Politecnico/current.csv`<br>`용접/_extracted/MILG_03_Spot_Welding/03_RESEARCH_ONLY_CC_BY_NC_4_0_Politecnico/force.csv`<br>`용접/_extracted/MILG_03_Spot_Welding/03_RESEARCH_ONLY_CC_BY_NC_4_0_Politecnico/labels.csv`<br>`용접/_extracted/MILG_03_Spot_Welding/03_RESEARCH_ONLY_CC_BY_NC_4_0_Politecnico/voltage.csv` | 비상업 연구용 (CC BY-NC) |
| WLD-03 | 용접 | `용접/_extracted/MILG_03_Spot_Welding/03_RESEARCH_ONLY_CC_BY_NC_4_0_Politecnico/current.csv`<br>`용접/_extracted/MILG_03_Spot_Welding/03_RESEARCH_ONLY_CC_BY_NC_4_0_Politecnico/force.csv`<br>`용접/_extracted/MILG_03_Spot_Welding/03_RESEARCH_ONLY_CC_BY_NC_4_0_Politecnico/labels.csv`<br>`용접/_extracted/MILG_03_Spot_Welding/03_RESEARCH_ONLY_CC_BY_NC_4_0_Politecnico/voltage.csv` | 비상업 연구용 (CC BY-NC) |

- MCT-02는 회사 설비 로그(DBexport)라 공개 데이터가 아닙니다. 공유 범위를 확인한 뒤 따로 전달하세요.
- `프레스/_extracted`, `용접/_extracted`는 원본 zip에서 필요한 파일만 꺼낸 것입니다.
- `experiments/`의 실험 코드는 결과(json)만 같이 넣었고, 실험에 쓴 원시 데이터(`_rows`)는 넣지 않았습니다.

## 설치와 실행

```bash
pip install -r requirements.txt
python engine.py --check "scenarios/*.yaml"   # 설정만 검사
python engine.py scenarios/inj_shot_anomaly.yaml   # 하나 실행
```
