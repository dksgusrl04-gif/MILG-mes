"""제조 용어 사전 — 칼럼 이름 · 설명 · 단위를 «무엇을 잰 값인가» 로 바꾸는 근거.

물리량(QUANTITY): 공정 조건 칼럼이 어떤 물리량인지 → 공정이 달라도 같은 물리량끼리 묶어 학습한다
역할(ROLE):       식별자 · 시간 · 결과(불량) · 결과 세부 · 수량 · 범주 · 공정 조건
단위(UNIT):       단위가 있으면 물리량을 강하게 알려 준다 ([K], (bar), rpm, °C ...)

한국어 · 영어 · 현장 약어를 같이 둔다. 처음 보는 이름은 문자 n-그램 유사도(schema.py)로 가장 가까운 항목을 찾는다.
"""

# 물리량: 키 → (한국어 이름, 키워드들)
QUANTITY = {
    'temperature': ('온도', ['temperature', 'temp', 'tmp', 'heat', 'thermal', '온도', '열', '존', 'zone', 'hz', 'furnace', '로', '소입', '템퍼', 'celsius', 'kelvin']),
    'pressure':    ('압력', ['pressure', 'pres', 'psi', 'bar', '압력', '압', 'pa']),
    'force':       ('힘 · 가압력', ['force', 'load cell', 'clamp', '가압', '하중', 'tonnage', 'newton', '절삭력', 'cutting force']),
    'current':     ('전류', ['current', 'current density', 'amp', 'ampere', 'amperage', 'cur', 'smc', '전류', 'ka']),
    'voltage':     ('전압', ['voltage', 'volt', 'esa voltage', '전압']),
    'speed':       ('속도 · 회전수', ['speed', 'rpm', 'rotational', 'spindle speed', 'spd', 'feed', 'velocity', '속도', '회전', '이송']),
    'torque':      ('토크', ['torque', 'nm', '토크', 'moment', '모멘트']),
    'time':        ('시간 · 주기', ['time', 'duration', 'cycle', 'dwell', 'ptime', 'weld_time', 'minute', 'sec', 'ms', '시간', '주기', '타발 시간', '체류']),
    'wear':        ('마모 · 사용량', ['wear', 'tool wear', 'vb', 'age', 'usage', 'life', 'run', '마모', '사용', '수명', '누적']),
    'load':        ('부하', ['load', 'sload', 'utilization', '부하']),
    'vibration':   ('진동 · 음향', ['vibration', 'vib', 'acoustic', 'ae', 'accel', '진동', '음향', '가속도']),
    'power':       ('출력 · 전력', ['power', 'output', 'watt', 'kw', 'heater', 'op', '출력', '전력', '히터']),
    'humidity':    ('습도', ['humidity', 'humifity', 'moisture', 'rh', '습도', '수분']),
    'viscosity':   ('점도', ['viscosity', 'visc', '점도']),
    'ratio':       ('비율 · 농도 · 첨가량', ['이탈 비율', '불량률', 'wax', 'hardener', 'additive', '첨가', 'pct', 'percent', 'ratio', 'rate', 'content', 'concentration', 'density', 'carbon', 'cp', '%', '비율', '농도', '함량', '포텐셜', '이탈률']),
    'dimension':   ('치수 · 두께', ['thickness', 'caliper', 'diameter', 'length', 'width', 'size', 'roughness', 'mm', '두께', '치수', '길이', '조도']),
    'hardness':    ('경도', ['hardness', 'durometer', 'hrc', 'hv', '경도']),
    'flow':        ('유량', ['flow', 'lpm', 'gpm', '유량']),
}

# 역할: 키 → (한국어, 이름 키워드)
ROLE_WORDS = {
    'id':       ('식별자', ['id', 'udi', 'uid', 'serial', 'sn', 'no', 'number', 'job', 'lot', 'code', '번호', '코드']),
    'time':     ('시각', ['timestamp', 'date', 'datetime', 'time stamp', 'start', 'end', 'at', '일시', '시각', '날짜']),
    'outcome':  ('결과 (불량 · 판정)', ['failure', 'fail', 'defect', 'bad', 'ng', 'reject', 'scrap', 'band type', 'label', 'class', 'target', 'pass/fail', 'ok', 'pass', 'good', '불량', '판정', '합격', '양품']),
    'exposure': ('수량 (분모)', ['qty', 'quantity', 'count', 'pieces', 'units', 'welds', 'parts', 'produced', '수량', '생산']),
    'category': ('범주 (설비 · 작업자 · 자재)', ['type', 'customer', 'mfg', 'vendor', 'supplier', 'division', 'location', 'press', 'unit', 'operator', 'worker', 'material', 'mat_lot', 'machine', 'equip', 'product', 'tank', 'blade', 'paper', 'ink', 'solvent', '작업자', '설비', '원자재', '품목', '자재']),
}
# 설정값 칼럼 (조건이 아니라 «목표» — 규격 비교에만 쓴다)
SETPOINT = ['sp', 'setpoint', 'set point', 'target value', '설정값', '설정', '목표값']
# 통계 종류: 평균(level) · 변동(spread) — 변동이 커지는 것은 공정이 달라도 «불안정» 이라는 같은 신호
SPREAD = ['std', 'sd', 'stdev', 'variance', 'var', 'range', 'iqr', '변동', '표준편차', '산포', '편차']
# 적산(누적) 값: 교체 · 정비 뒤 0 에서 시작해 계속 더해지는 값 (사용 시간 · 타발 수 · 가공 수 …)
ACCUM = ['누적', '적산', 'total', 'cumulative', 'cum', 'since', 'usage', 'used', 'life', 'tool wear', '사용 시간', '사용량', '수명', '총']
# 글(비정형) 칼럼 이름
TEXT_WORDS = ['memo', 'comment', 'note', 'notes', 'remark', 'remarks', 'description', 'message', 'log', 'text', '메모', '비고', '내용', '설명', '특이사항', '기록', '사유', '문구']
# 작업 메모 · 알람 문장에서 이상 징후를 뜻하는 말 (사전 지식 — 학습 전에도 쓰는 «경험 규칙»)
ALARM_WORDS = ['이상', '소음', '진동', '떨림', '누설', '누유', '과열', '발열', '파손', '깨짐', '크랙', '균열', '막힘', '걸림', '마모', '편마모', '찍힘', '버',
               '변형', '휨', '들뜸', '불량', '재작업', '교체', '긴급', '정지', '알람', '경보', '튐', '스파크', '냄새', '연기',
               'noise', 'vibration', 'chatter', 'leak', 'overheat', 'crack', 'broken', 'jam', 'stuck', 'worn', 'burr', 'defect', 'alarm', 'smoke', 'spark']
# 결과 칼럼 중 «합격 · 양품» 처럼 1 이 좋은 쪽인 이름 (부호를 뒤집는다)
GOOD_POLARITY = ['ok', 'pass', 'good', 'noband', 'no band', '합격', '양품', 'normal']
BAD_POLARITY = ['ng', 'nok', 'nio', 'fail', 'failure', 'defect', 'reject', 'bad', 'scrap', '불량', '불합격', '부적합', '아님', '이상']      # 이름에 함께 있으면 «좋은 쪽» 단어가 있어도 사건 쪽 (예: «OK 아님», 불합격)
# 결과의 «세부 유형» (TWF · HDF 같은 고장 모드) — 결과와 같은 사건을 다르게 적은 것이라 입력으로 쓰면 누설
OUTCOME_DETAIL = ['twf', 'hdf', 'pwf', 'osf', 'rnf', 'mode', 'defect type', 'fault', 'cause', '유형', '사유']

# 단위 → 물리량
UNIT = {
    'k': 'temperature', '°c': 'temperature', 'c': 'temperature', 'degc': 'temperature', '℃': 'temperature', 'f': 'temperature',
    'bar': 'pressure', 'mpa': 'pressure', 'kpa': 'pressure', 'pa': 'pressure', 'psi': 'pressure',
    'n': 'force', 'kn': 'force', 'kgf': 'force', 'ton': 'force',
    'a': 'current', 'ka': 'current', 'ma': 'current',
    'v': 'voltage', 'kv': 'voltage',
    'rpm': 'speed', 'mm/min': 'speed', 'm/s': 'speed', 'mm/s': 'speed',
    'nm': 'torque', 'n·m': 'torque',
    's': 'time', 'sec': 'time', 'ms': 'time', 'min': 'time', 'h': 'time', 'cyc': 'time',
    'w': 'power', 'kw': 'power',
    '%rh': 'humidity',
    'cp': 'viscosity', 'cps': 'viscosity', 'pa·s': 'viscosity',
    '%': 'ratio', '%c': 'ratio', 'ppm': 'ratio',
    'mm': 'dimension', 'um': 'dimension', 'µm': 'dimension', 'inch': 'dimension',
    'hrc': 'hardness', 'hv': 'hardness',
    'l/min': 'flow', 'lpm': 'flow',
}
