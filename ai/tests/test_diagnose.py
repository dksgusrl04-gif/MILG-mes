"""파일 진단과 8D 초안의 판단 범위 — 서버의 OS별 잠금 기능 없이 검증."""

def test_diagnose_finds_planted_cause():
    """진단: 심어 둔 원인(온도 높은 구간)을 1순위로 찾고, 상관없는 칼럼은 올리지 않는다"""
    import numpy as np
    import pandas as pd
    from mlng_ai.diagnose import diagnose, render_html
    r = np.random.default_rng(3)
    n = 1500
    t, noise = r.normal(850, 10, n), r.normal(0, 1, n)
    bad = (r.random(n) < 0.02 + 0.2 * (t > 860)).astype(int)
    df = pd.DataFrame({'측정시각': pd.date_range('2026-01-01', periods=n, freq='h').astype(str), 'lot_no': [f'L{i}' for i in range(n)],
                       '소입 온도 ℃': t, '잡음 센서': noise, 'NG': bad})
    res = diagnose(df, unit_cost=10000, name='t.csv')
    assert res['outcome'] == 'NG'
    assert res['factors'][0]['column'] == '소입 온도 ℃' and res['factors'][0]['lift'] > 2
    assert '잡음 센서' not in [f['column'] for f in res['factors'][:2]]
    assert res['copq']['total'] == bad.sum() * 10000
    assert '불량 원인 후보' in render_html(res)


def test_diagnose_without_outcome():
    import pandas as pd
    from mlng_ai.diagnose import diagnose
    res = diagnose(pd.DataFrame({'온도': range(50), '압력': range(50)}))
    assert res['outcome'] == '' and res['readiness']['score'] < 50 and res['next']


def test_eight_d_report():
    """관측 상관만으로 운전 설정값이나 불량 감소 목표를 처방하지 않는다."""
    import numpy as np
    import pandas as pd
    from mlng_ai.diagnose import diagnose, render_html
    r = np.random.default_rng(5)
    t = r.normal(850, 10, 1200)
    df = pd.DataFrame({'소입 온도': t, '판정': np.where(r.random(1200) < 0.02 + 0.2 * (t > 860), '불합격', '합격')})
    res = diagnose(df, unit_cost=5000)
    d = res['report_8d']
    assert d['d4'][0]['column'] == '소입 온도'
    assert d['d5'][0]['target'] is None and d['d5'][0]['target_rate'] is None
    assert d['goal_rate'] is None and all(x['goal'] is None for x in d['d6'])
    assert '공정 담당자 승인 후 검증' in d['d5'][0]['action']
    report = render_html(res)
    assert '원인 분석 보고서 초안 (8D)' in report and '목표·절감액은 검증 후 확정' in report
    # 예전 캐시를 출력해도 가정한 감소율이 검증된 목표로 복원되지 않는다.
    res['report_8d']['goal_rate'] = 0.01
    res['report_8d']['d5'][0]['action'] = '과거의 미검증 운전 처방'
    assert '과거의 미검증 운전 처방' not in render_html(res)
