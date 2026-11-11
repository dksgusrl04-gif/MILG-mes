"""사출 · 프레스 · 용접 학습 팩 — 요약 · 표 · 검증 흐름 (작은 가상 데이터)"""
import io
import json

import numpy as np
import pandas as pd

from mlng_ai import packs


def test_block_matches_curve_shape():
    A = np.vstack([np.linspace(0, 10, 100), np.linspace(10, 0, 100)])
    r = packs.block(A, 'force')
    assert r['force_max'].tolist() == [10.0, 10.0] and r['force_peakpos'].tolist() == [1.0, 0.0]
    assert r['force_segslope1'][0] > 0 > r['force_segslope1'][1] and r['force_seg5'][0] > r['force_seg1'][0]


def test_json_rows_streams_split_format_with_nulls():
    doc = json.dumps({'columns': ['0', '1', '2'], 'index': [0, 1, 2], 'data': [[1.5, 2, 3], [4, None, 6], [7, 8, 9]]}).encode()
    rows = [packs._nums(s) for s in packs._json_rows(io.BytesIO(doc), chunk=7)]
    assert len(rows) == 3 and rows[0].tolist() == [1.5, 2.0, 3.0] and np.isnan(rows[1][1]) and rows[2][2] == 9.0


def test_tables_and_evaluate_hold_out_whole_groups(tmp_path):
    rng = np.random.default_rng(3)
    n = 240
    wear = np.tile(np.linspace(0, 1, n // 4), 4)
    d = pd.DataFrame({'stroke_id': [f's{i}' for i in range(n)], 'tool_id': np.repeat(['E1', 'E2', 'E3', 'E4'], n // 4), 'use_count': np.tile(np.arange(n // 4), 4), 'wear': wear})
    for k, v in packs.block(100 + 40 * wear[:, None] * np.linspace(0.5, 1, 60) + rng.normal(0, 2, (n, 60)), 'force').items():
        d[k] = v
    d.to_csv(tmp_path / 'press_fineblank.csv', index=False)
    T = packs.load_tables(tmp_path)
    t, G, info = T['press_fineblank']
    assert set(G) == {'E1', 'E2', 'E3', 'E4'} and 'wear' not in t.df and t.df.tool_ng.sum() > 0
    r = packs.evaluate(t, G, info)
    assert r['groups'] == 4 and r['models']['plus']['auc'] > 0.8 and r['models']['boost']['auc'] > 0.8
    assert 'use_count' not in packs.report_md({'data': {'press_fineblank': r}})
