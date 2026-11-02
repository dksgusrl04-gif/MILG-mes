"""칼럼 해석 정답표 — 사람이 원본 설명서를 보고 정한 역할 · 물리량 (해석기 정확도 검증용, 학습에는 쓰지 않는다)
'*' = 어느 물리량이든 맞음으로 친다 (원래 뜻이 애매한 칼럼)"""
import re

RULES = {
    'heat': [(r'^lot_id$', 'id'), (r'^start_time$', 'time'), (r'^qty$', 'exposure'), (r'^bad_count$', 'outcome'), (r'^worker$', 'category'), (r'^mat_lot$', ('id', 'category')),
             (r'^(wash|hz\d|sb\d|cv\d|dr\d)_(mean|std)$', 'param:temperature'), (r'^cp_(mean|std)$', 'param:ratio'), (r'_out$', ('param:ratio', 'param:event', 'ignore')),
             (r'^heater_', 'param:power')],
    'press': [(r'^lot_id$', 'id'), (r'^work_date$', 'time'), (r'^qty$', 'exposure'), (r'^bad_count$', 'outcome'), (r'^strokes$', ('param:*', 'exposure')),
              (r'^changeover$', 'param:event'), (r'^ptime_(mean|std)$', 'param:time'), (r'^p\d_(mean|std)$', 'param:pressure'), (r'_sp$', ('setpoint', 'ignore'))],
    'weld': [(r'^lot_id$', 'id'), (r'^work_date$', 'time'), (r'^welds$', 'exposure'), (r'^bad_count$', 'outcome'), (r'^out_rate$', 'param:ratio'), (r'^excursion$', 'param:event')],
    'mach': [(r'^run_time$', 'time'), (r'^(tool_id|lot)$', 'id'), (r'^cut_minutes$', ('param:time', 'param:wear')), (r'^vb_', ('param:wear', 'outcome_detail')),
             (r'^depth_of_cut$', 'param:dimension'), (r'^feed$', 'param:speed'), (r'^work_material$', 'category'), (r'^quality_ng$', 'outcome'),
             (r'^smc', 'param:current'), (r'^(vib|AE)_', 'param:vibration')],
    'insp': [(r'^serial_no$', 'id'), (r'^inspect_time$', 'time'), (r'^lot$', 'id'), (r'^pass_ok$', 'outcome'), (r'^spd_', 'param:speed'), (r'load', 'param:load'), (r'cur', 'param:current')],
    'ai4i': [(r'^(UDI|Product ID)$', 'id'), (r'^Type$', 'category'), (r'temperature', 'param:temperature'), (r'Rotational', 'param:speed'), (r'Torque', 'param:torque'),
             (r'Tool wear', ('param:wear', 'param:time')), (r'^Machine failure$', 'outcome'), (r'^(TWF|HDF|PWF|OSF|RNF)$', 'outcome_detail')],
    'bands': [(r'^timestamp$', 'time'), (r'^(cylinder number|job number)$', 'id'), (r'^band type$', 'outcome'),
              (r'^(customer|grain screened|ink color|proof on ctd ink|blade mfg|cylinder division|paper type|ink type|direct steam|solvent type|type on cylinder|press type|press|unit number|cylinder size|paper mill location|plating tank)$', 'category'),
              (r'^proof cut$', 'param:*'), (r'^viscosity$', 'param:viscosity'), (r'^(caliper|roughness)$', 'param:dimension'), (r'^ink temperature$', 'param:temperature'),
              (r'^humifity$', 'param:humidity'), (r'^blade pressure$', 'param:pressure'), (r'pct$|^(wax|hardener|anode space ratio|chrome content)$', 'param:ratio'),
              (r'^press speed$', 'param:speed'), (r'^ESA Voltage$', 'param:voltage'), (r'^(ESA Amperage|current density)$', 'param:current'), (r'^roller durometer$', 'param:hardness')],
}


def truth(key: str, col: str):
    for pat, lab in RULES.get(key, []):
        if re.search(pat, col):
            return lab if isinstance(lab, tuple) else (lab,)
    return None


def score(sense, labels) -> bool:
    got_role, got_q = sense.role, sense.quantity
    for lab in labels:
        role, _, q = lab.partition(':')
        if role != got_role:
            continue
        if not q or q == '*' or q == got_q or (q == 'event' and sense.stat == 'event'):
            return True
    return False
