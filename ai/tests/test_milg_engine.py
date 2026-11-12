"""시나리오 엔진(milg_engine) + MILG 판정 엔진(mlng_ai) 통합 — 후보 «milg» 가 같은 분할 · 같은 지표로 비교되는지"""
import glob
import json
import os
import sys

import numpy as np
import pandas as pd
import pytest

ENG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'milg_engine')
sys.path.insert(0, ENG)
pytest.importorskip('yaml')
import engine  # noqa: E402
from milg_core import MilgEngineClassifier  # noqa: E402


def _data(n=600, seed=0):
    rng = np.random.default_rng(seed)
    d = pd.DataFrame({'shot': np.arange(n), 'day': np.repeat([f'd{i}' for i in range(6)], n // 6), 'temp': rng.normal(200, 5, n), 'press': rng.normal(80, 3, n), 'noise': rng.normal(0, 1, n)})
    p = 1 / (1 + np.exp(-(np.abs(d.temp - 200) / 5 * 2.2 - 3.5)))          # 온도가 적정 범위를 벗어나면(양쪽) 불량
    d['result'] = np.where(rng.random(n) < p, 'NG', 'OK')
    return d


def test_shipped_scenarios_pass_schema_check():
    for p in glob.glob(os.path.join(ENG, 'scenarios', '*.yaml')):
        engine.load_scenario(p)


def test_milg_classifier_finds_two_sided_cause():
    d = _data()
    X, y = d[['temp', 'press', 'noise']], (d.result == 'NG').astype(int)
    m = MilgEngineClassifier().fit(X[:400], y[:400])
    p = m.predict_proba(X[400:])[:, 1]
    assert p.min() >= 0 and p.max() <= 1 and p[y[400:] == 1].mean() > p[y[400:] == 0].mean()
    assert m.feature_importances_.argmax() == 0
    row = X[400:][(np.abs(X.temp[400:] - 200) > 9).to_numpy()].head(1)
    assert m.explain(row)[0][0]['feature'] == 'temp'


def test_scenario_run_compares_milg_with_other_candidates(tmp_path):
    _data().to_csv(tmp_path / 'shots.csv', index=False)
    (tmp_path / 's.yaml').write_text('''
scenario: T-01
process: 시험
title: 통합 시험
question: 불량인가?
data_root: .
unit: {type: table, path: shots.csv, id: shot}
task: binary
label: {source: column, column: result, positive: [NG]}
features: {exclude: [shot, day]}
split: {method: group, group: day, test_groups: [d5]}
model: {candidates: [logistic, hgb, milg]}
decision: {type: threshold_for_recall, target_recall: 0.8}
output: {carry_columns: [day]}
''', encoding='utf-8')
    r = engine.run(str(tmp_path / 's.yaml'), str(tmp_path / 'out'), 1e9)
    assert set(r['cv_scores']) == {'baseline', 'logistic', 'hgb', 'milg'} and set(r['test_candidates']) == {'logistic', 'hgb', 'milg'}
    assert r['test_candidates']['milg']['roc_auc'] > 0.7 > r['test_candidates']['logistic']['roc_auc'] + 0.0      # 양쪽 벗어남은 선형(logistic)이 못 잡는다
    out = pd.read_csv(tmp_path / 'out' / 'T-01' / 'predictions.csv')
    assert {'decision', 'score', 'model', 'engine_version'} <= set(out.columns)
    if r['best_model'] == 'milg':
        assert out.loc[out.decision == 'NG', 'cause_candidates'].str.contains('temp').mean() > 0.5
    json.loads((tmp_path / 'out' / 'T-01' / 'report.json').read_text(encoding='utf-8'))


def test_milg_rejected_for_non_binary(tmp_path):
    (tmp_path / 's.yaml').write_text('scenario: T-02\nprocess: 시험\ntitle: t\nquestion: q\nunit: {type: table, path: a.csv}\ntask: regression\nlabel: {source: column, column: y}\nmodel: {candidates: [ridge, milg]}\n', encoding='utf-8')
    with pytest.raises(engine.ConfigError):
        engine.load_scenario(str(tmp_path / 's.yaml'))


def _anom(tmp_path, cands):
    rng = np.random.default_rng(1)
    n = 900
    d = pd.DataFrame({'u': np.arange(n), 't': pd.date_range('2026-01-01', periods=n, freq='min').astype(str), 'tool': np.tile(['A', 'B', 'C'], n // 3)})
    lvl = d.tool.map({'A': 10.0, 'B': 30.0, 'C': 60.0}).to_numpy()
    for c in ['i1', 'i2', 'i3', 'v1', 'v2', 'v3']:
        d[c] = lvl + rng.normal(0, 1, n)
    bad = np.zeros(n, bool); bad[rng.choice(np.arange(650, n), 25, replace=False)] = True
    d.loc[bad, ['i1', 'i2', 'i3', 'v1', 'v2', 'v3']] += 2.2          # 여러 특성이 조금씩 함께 벗어남
    d['label'] = np.where(bad, 'NG', 'OK')
    d.to_csv(tmp_path / 'u.csv', index=False)
    (tmp_path / 'a.yaml').write_text(f'''
scenario: A-01
process: 시험
title: 이상 탐지 통합 시험
question: 평소와 다른가?
data_root: .
unit: {{type: table, path: u.csv, id: u}}
task: anomaly
features: {{exclude: [u, t, tool, label]}}
split: {{method: time, column: t, test_size: 0.33}}
anomaly:
  candidates: {cands}
  context: [tool]
  label_eval: {{column: label, positive: [NG]}}
  sensitivity:
    - {{name: 전류, columns: ["i*"], factors: [1.2]}}
output: {{carry_columns: [tool]}}
''', encoding='utf-8')
    return engine.run(str(tmp_path / 'a.yaml'), str(tmp_path / 'out'), 1e9), pd.read_csv(tmp_path / 'out' / 'A-01' / 'predictions.csv')


def test_anomaly_compares_milg_normal_mode_side_by_side(tmp_path):
    r, out = _anom(tmp_path, '[context, milg]')
    c = r['candidates']
    assert list(c) == ['context', 'milg'] and c['context']['primary'] and r['best_model'].startswith('context')
    assert c['milg']['label_eval']['auc'] > 0.9 and c['milg']['label_eval']['test_defects'] == 25
    assert c['milg']['sensitivity'] and {'score_milg', 'decision_milg'} <= set(out.columns)
    assert abs(c['milg']['baseline']['ng_rate'] - 0.01) < 0.01          # 같은 분위수 임계값


def test_anomaly_milg_as_primary_drives_decision(tmp_path):
    r, out = _anom(tmp_path, '[milg, context]')
    assert r['best_model'] == 'milg_normal' and set(out.model) == {'milg_normal'} and 'score_context' in out.columns
    assert r['label_eval']['auc'] == r['candidates']['milg']['label_eval']['auc']
    assert out.loc[out.decision == 'NG', 'reason'].str.contains('가장 크게 벗어남').all()
