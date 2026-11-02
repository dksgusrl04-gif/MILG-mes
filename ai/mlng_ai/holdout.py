"""칼럼 해석 «처음 보는 이름» 시험 — 사전을 고칠 때 보지 않은 칼럼 이름 72개 (사출 · 주조 · 도장 · 용접 · 가공 · 조립 · 반도체)
값은 이름에 맞는 그럴듯한 범위로 만든다 (난수 고정). 정답 형식은 truth.py 와 같다. 이 목록을 보고 사전을 고치면 이 시험은 의미가 없어진다."""
import numpy as np
import pandas as pd

# (칼럼 이름, 설명, 단위, 값 종류, 정답)
#   값 종류: ('num', 평균, 표준편차) · 'bin' · ('cat', [값…]) · 'id' · 'time' · ('cnt', 평균)
ITEMS = [
    ('Barrel_Temp_Z3', '', '', ('num', 230, 3), 'param:temperature'), ('MoldTempFixed', '', '', ('num', 60, 1.5), 'param:temperature'),
    ('melt_temperature_C', '', '', ('num', 245, 2), 'param:temperature'), ('InjPressMax_bar', '', '', ('num', 1100, 30), 'param:pressure'),
    ('hold_pressure', '보압', 'MPa', ('num', 60, 2), 'param:pressure'), ('Inj_Speed', '사출 속도', 'mm/s', ('num', 80, 4), 'param:speed'),
    ('screw_rpm', '', '', ('num', 120, 5), 'param:speed'), ('CoolingTime', '냉각 시간', 's', ('num', 18, 0.5), 'param:time'),
    ('cycle_time_sec', '', '', ('num', 42, 1), 'param:time'), ('clamp_force_kN', '', '', ('num', 1500, 20), 'param:force'),
    ('cushion_mm', '쿠션량', 'mm', ('num', 5, 0.3), 'param:dimension'), ('dryer_dewpoint', '건조기 이슬점', '°C', ('num', -40, 2), 'param:temperature'),
    ('Shot_Count', '', '', 'id', 'id'), ('MachineNo', '사출기 번호', '', ('cat', ['IM-01', 'IM-02', 'IM-03']), 'category'),
    ('Resin_Lot', '수지 LOT', '', ('cat', ['R1', 'R2', 'R3', 'R4']), ('category', 'id')), ('Short_Shot', '미성형 불량', '', 'bin', 'outcome'),
    ('pour_temp', '주탕 온도', '℃', ('num', 1420, 8), 'param:temperature'), ('sand_moisture_pct', '', '', ('num', 3.2, 0.2), ('param:humidity', 'param:ratio')),
    ('mold_hardness', '주형 경도', '', ('num', 85, 2), 'param:hardness'), ('Si_content', '규소 함량', '%', ('num', 2.1, 0.05), 'param:ratio'),
    ('porosity_ng', '기공 불량', '', 'bin', 'outcome'), ('ladle_id', '', '', ('cat', ['L1', 'L2']), ('category', 'id')),
    ('booth_humidity', '도장 부스 습도', '%RH', ('num', 55, 3), 'param:humidity'), ('paint_viscosity_sec', '도료 점도', '', ('num', 22, 0.8), 'param:viscosity'),
    ('oven_temp_set', '', '', ('num', 180, 0), 'ignore'), ('oven_temp_act', '건조로 실제 온도', '°C', ('num', 178, 2), 'param:temperature'),
    ('film_thickness_um', '도막 두께', 'µm', ('num', 35, 2), 'param:dimension'), ('conveyor_speed', '', 'm/min', ('num', 3, 0.1), 'param:speed'),
    ('gun_voltage_kV', '', '', ('num', 70, 2), 'param:voltage'), ('appearance_result', '외관 판정 (OK/NG)', '', ('cat', ['OK', 'NG']), 'outcome'),
    ('WeldCurrent_kA', '', '', ('num', 10.5, 0.3), 'param:current'), ('electrode_force', '전극 가압력', 'kN', ('num', 3.2, 0.1), 'param:force'),
    ('squeeze_time_cyc', '', '', ('num', 20, 1), 'param:time'), ('tip_dress_count', '팁 드레싱 후 타점 수', '', ('num', 300, 80), ('param:wear', 'param:*')),
    ('arc_volt', '', '', ('num', 24, 0.6), 'param:voltage'), ('wire_feed_rate', '와이어 송급 속도', 'm/min', ('num', 8, 0.3), 'param:speed'),
    ('gas_flow_lpm', '', '', ('num', 15, 0.5), 'param:flow'), ('nugget_dia_mm', '너깃 직경', 'mm', ('num', 5.5, 0.2), ('param:dimension', 'outcome_detail')),
    ('weld_crack', '크랙 여부', '', 'bin', 'outcome'), ('robot_id', '', '', ('cat', ['R01', 'R02', 'R03']), 'category'),
    ('SpindleLoad_pct', '', '', ('num', 45, 4), 'param:load'), ('feedrate_mm_min', '', '', ('num', 800, 20), 'param:speed'),
    ('coolant_conc', '절삭유 농도', '%', ('num', 6, 0.3), 'param:ratio'), ('coolant_temp', '', '°C', ('num', 24, 0.6), 'param:temperature'),
    ('tool_life_used_min', '공구 사용 시간', 'min', ('num', 120, 40), ('param:wear', 'param:time')), ('vibration_rms', '', 'g', ('num', 0.3, 0.05), 'param:vibration'),
    ('acoustic_emission', '', '', ('num', 1.2, 0.2), 'param:vibration'), ('bore_dia_meas', '보어 직경 측정', 'mm', ('num', 30.0, 0.01), ('param:dimension', 'outcome_detail')),
    ('surface_Ra', '표면 조도', 'µm', ('num', 0.8, 0.05), ('param:dimension', 'outcome_detail')), ('judge', '최종 판정 (1=합격)', '', 'bin', 'outcome'),
    ('operator_name', '작업자', '', ('cat', ['김', '이', '박', '최']), 'category'), ('shift', '근무조', '', ('cat', ['A', 'B', 'C']), 'category'),
    ('torque_Nm', '체결 토크', '', ('num', 25, 0.6), 'param:torque'), ('angle_deg', '체결 각도', 'deg', ('num', 45, 3), 'param:*'),
    ('press_fit_force', '압입 하중', 'N', ('num', 4200, 120), 'param:force'), ('leak_rate', '누설량', 'cc/min', ('num', 0.8, 0.1), ('param:flow', 'param:ratio', 'outcome_detail')),
    ('assy_ng_flag', '', '', 'bin', 'outcome'), ('station', '공정 스테이션', '', ('cat', ['ST10', 'ST20', 'ST30']), 'category'),
    ('chamber_pressure_mTorr', '', '', ('num', 50, 2), 'param:pressure'), ('rf_power_W', '', '', ('num', 500, 10), 'param:power'),
    ('wafer_temp', '', '', ('num', 65, 1), 'param:temperature'), ('etch_time', '', 's', ('num', 90, 2), 'param:time'),
    ('gas_ratio_CF4_O2', '', '', ('num', 0.8, 0.03), 'param:ratio'), ('chiller_flow', '', 'L/min', ('num', 12, 0.4), 'param:flow'),
    ('wafer_id', '', '', 'id', 'id'), ('yield_fail', '수율 불량', '', 'bin', 'outcome'),
    ('timestamp_utc', '', '', 'time', 'time'), ('prod_date', '생산일', '', 'time', 'time'), ('good_qty', '양품 수량', '', ('cnt', 900), ('exposure', 'outcome_detail', 'param:*')),
    ('input_qty', '투입 수량', '', ('cnt', 1000), 'exposure'), ('defect_qty', '불량 수량', '', ('cnt', 3), 'outcome'), ('line_speed_spm', '라인 속도 (분당 타수)', 'spm', ('num', 40, 1), 'param:speed'),
]


def table(seed: int = 7, n: int = 300) -> tuple[pd.DataFrame, dict, dict]:
    r = np.random.default_rng(seed)
    cols, meta, truth = {}, {}, {}
    t0 = pd.Timestamp('2026-03-01')
    for name, label, unit, kind, lab in ITEMS:
        if isinstance(kind, tuple) and kind[0] == 'num':
            v = r.normal(kind[1], kind[2], n)
        elif isinstance(kind, tuple) and kind[0] == 'cat':
            v = r.choice(kind[1], n)
        elif isinstance(kind, tuple) and kind[0] == 'cnt':
            v = r.poisson(kind[1], n)
        elif kind == 'bin':
            v = (r.random(n) < 0.06).astype(int)
        elif kind == 'id':
            v = np.arange(100000, 100000 + n)
        else:
            v = [str(t0 + pd.Timedelta(minutes=7 * i)) for i in range(n)]
        cols[name] = v
        meta[name] = {'label': label, 'unit': unit}
        truth[name] = lab if isinstance(lab, tuple) else (lab,)
    return pd.DataFrame(cols), meta, truth


def evaluate() -> dict:
    """역할 · 물리량을 이름만 보고 맞힌 비율 (한 표에 결과 칼럼이 여럿이면 하나만 결과가 되므로 칼럼 하나씩 따로 해석)"""
    from .schema import sense
    from .truth import score
    df, meta, truth = table()
    rows = []
    for c in df.columns:
        s = sense(c, df[c], **meta[c])
        rows.append({'column': c, 'label': meta[c]['label'], 'role': s.role, 'quantity': s.quantity, 'confidence': s.confidence, 'truth': '/'.join(truth[c]), 'ok': score(s, truth[c])})
    acc = float(np.mean([r['ok'] for r in rows]))
    hi = [r for r in rows if r['confidence'] >= 0.7]
    return {'n': len(rows), 'accuracy': acc, 'high_conf_n': len(hi), 'high_conf_acc': float(np.mean([r['ok'] for r in hi])) if hi else float('nan'), 'rows': rows}
