"""서버용 서비스 — FastAPI(server/api) 가 부른다. 웹 프레임워크와 무관한 순수 Python.

  map(df, meta)                   처음 보는 표의 칼럼 해석
  update(process, df, meta)       새 데이터로 누적 학습 1회: 먼저 예측해 채점 → 그다음 학습 → 상태 저장
  predict(process, df, meta)      학습 없이 위험도만 — 행마다 기대 불량 · 확률 · 키운 쪽(슬롯 · 칼럼)
  diagnose(df, meta, unit_cost)   데이터 진단 리포트 (칼럼 해석 · 품질 · 원인 후보 · 손실)
상태: <state_dir>/model.json (누적 모델) · <state_dir>/rows/<공정>.csv (공정마다 받은 원본 — «평소 값» 계산용)
"""
from __future__ import annotations

import fcntl
import json
import re
import threading
from contextlib import contextmanager
from pathlib import Path

import numpy as np
import pandas as pd

from .datasets import Table
from .engine import poisson_dev
from .features import SLOT_KO, SLOTS, encode, standardize
from .model import CumulativeModel
from .schema import sense_table

KEY = re.compile(r'^[a-z][a-z0-9_]{1,31}$')


class Service:
    def __init__(self, state_dir: str | Path, seed_model: str | Path | None = None):
        self.dir = Path(state_dir)
        (self.dir / 'rows').mkdir(parents=True, exist_ok=True)
        self.path = self.dir / 'model.json'
        self.lock = threading.Lock()
        if self.path.exists():
            self.M = CumulativeModel.load(self.path)
        elif seed_model and Path(seed_model).exists():
            self.M = CumulativeModel.load(seed_model)
        else:
            self.M = CumulativeModel(SLOTS)

    @contextmanager
    def _exclusive(self):
        """서버 작업자(프로세스)가 여럿이어도 한 번에 하나만 갱신 — 파일 잠금 + 최신 상태 다시 읽기"""
        with self.lock, open(self.dir / '.lock', 'w') as lf:
            fcntl.flock(lf, fcntl.LOCK_EX)
            try:
                if self.path.exists():
                    self.M = CumulativeModel.load(self.path)
                yield
            finally:
                fcntl.flock(lf, fcntl.LOCK_UN)

    def status(self) -> dict:
        if self.path.exists():
            self.M = CumulativeModel.load(self.path)
        return {'version': self.M.version, 'processes': [{'key': p, 'rows': self.M.seen.get(p, 0), 'gate': round(self.M.gate_weight(p), 3),
                                                          'base_rate': self.M.base_rate(p)} for p in self.M.procs]}

    @staticmethod
    def map(df: pd.DataFrame, meta: dict | None = None) -> list[dict]:
        return [s.to_dict() for s in sense_table(df, meta or {}).values()]

    def update(self, process: str, df: pd.DataFrame, meta: dict | None = None, time_col: str | None = None) -> dict:
        if not KEY.match(process):
            raise ValueError('공정 키는 영문 소문자 · 숫자 · _ (2~32자)')
        if len(df) == 0:
            raise ValueError('행이 없습니다')
        with self._exclusive():
            hp = self.dir / 'rows' / f'{process}.csv'
            old = pd.read_csv(hp) if hp.exists() else pd.DataFrame()
            if len(old) and set(old.columns) != set(df.columns):
                raise ValueError(f'칼럼이 이전과 다릅니다: 이전 {sorted(old.columns)[:8]}…')
            allr = pd.concat([old, df], ignore_index=True)
            e = encode(Table(process, process, allr, meta or {}, {}, '', {}, 'row'))
            Xs = standardize(e.X.reindex(columns=SLOTS, fill_value=0.0))[SLOTS].to_numpy()
            new = np.arange(len(old), len(allr))
            X, y, n = Xs[new], e.y[new], e.n[new]
            out = {'process': process, 'rows': int(len(new)), 'events': float(y.sum())}
            if process in self.M.procs and self.M.seen.get(process, 0) > 0:
                mu, w = self.M.predict(process, X, n)[0], self.M.gate_weight(process)
                mg = w * mu + (1 - w) * self.M.base_rate(process) * n
                bi = bool(self.M.binary.get(process))
                d0 = poisson_dev(y, self.M.base_rate(process) * n, bi)
                out.update({'scored': True, 'gate': w, 'dev_skill': float(1 - poisson_dev(y, mg, bi) / d0) if d0 > 0 else None,
                            'pred': [float(v) for v in mg[:200]]})
            else:
                self.M.add_process(process, (y.sum() + 0.5) / (n.sum() + 1))
                out['scored'] = False
            self.M.update([(process, X, y, n)])
            allr.to_csv(hp, index=False)
            self.M.save(self.path)
            out['version'] = self.M.version
            out['columns'] = {c: {'role': s.role, 'quantity': s.quantity} for c, s in e.senses.items()}
            return json.loads(json.dumps(out, default=float))

    def _encode_new(self, process: str, df: pd.DataFrame, meta: dict | None):
        """저장된 원본 뒤에 새 행을 붙여 «평소 값» 기준으로 인코딩 → 새 행 부분만 돌려준다 (저장은 안 함)"""
        hp = self.dir / 'rows' / f'{process}.csv'
        old = pd.read_csv(hp) if hp.exists() else pd.DataFrame()
        if len(old) and set(old.columns) != set(df.columns):
            raise ValueError(f'칼럼이 이전과 다릅니다: 이전 {sorted(old.columns)[:8]}…')
        allr = pd.concat([old, df], ignore_index=True)
        e = encode(Table(process, process, allr, meta or {}, {}, '', {}, 'row'))
        Xs = standardize(e.X.reindex(columns=SLOTS, fill_value=0.0))[SLOTS].to_numpy()
        new = np.arange(len(old), len(allr))
        return e, Xs[new], e.n[new], new

    def predict(self, process: str, df: pd.DataFrame, meta: dict | None = None, top: int = 3) -> dict:
        if not KEY.match(process):
            raise ValueError('공정 키는 영문 소문자 · 숫자 · _ (2~32자)')
        if len(df) == 0:
            raise ValueError('행이 없습니다')
        if self.path.exists():
            self.M = CumulativeModel.load(self.path)
        e, X, n, new = self._encode_new(process, df, meta)
        known = process in self.M.procs and self.M.seen.get(process, 0) > 0
        if known:
            mu, w = self.M.predict(process, X, n)[0], self.M.gate_weight(process)
            ex = w * mu + (1 - w) * self.M.base_rate(process) * n
            beta = None
        else:                                     # 처음 보는 공정: 공통 효과로 «위험 순위» 만 (학습 0건)
            beta = np.asarray(self.M.m[:self.M.K])
            z = X @ beta
            ex, w = np.exp(np.clip(z, -6, 6)), 0.0
        rows = []
        for i in range(len(new)):
            con = self.M.explain(process, X[i]) if known else sorted(
                [{'slot': s, 'contrib': float(beta[j] * X[i, j])} for j, s in enumerate(SLOTS) if X[i, j] != 0], key=lambda r: -r['contrib'])
            tops = []
            for r in con[:top]:
                s, src = r['slot'], ''
                if s in e.detail and not s.startswith('up:'):
                    cols, am = e.detail[s]
                    src = str(cols[int(np.asarray(am)[new[i]])])
                tops.append({'slot': s, 'name': SLOT_KO.get(s, s), 'column': src, 'contrib': round(float(r['contrib']), 4)})
            row = {'i': i, 'expected': float(ex[i]) if known else None, 'risk': float(ex[i]) if not known else float(ex[i] / max(n[i], 1e-9)), 'top': tops}
            if known and n.max() <= 1:
                row['p'] = float(1 - np.exp(-ex[i]))
            rows.append(row)
        return json.loads(json.dumps({'process': process, 'mode': 'learned' if known else 'zero_shot', 'gate': float(w), 'rows': rows}, default=float))

    def diagnose(self, df: pd.DataFrame, meta: dict | None = None, unit_cost: float | None = None, name: str = '') -> dict:
        from .diagnose import diagnose
        if self.path.exists():
            self.M = CumulativeModel.load(self.path)
        return json.loads(json.dumps(diagnose(df, meta, unit_cost=unit_cost, model=self.M, name=name), default=float))

    def forget(self, process: str) -> dict:
        """공정 원본 행 파기 + 공정별 효과 초기화 (공통 효과에 섞인 몫은 통계량이라 개별 행으로 되돌릴 수 없음)"""
        if not KEY.match(process):
            raise ValueError('공정 키는 영문 소문자 · 숫자 · _ (2~32자)')
        with self._exclusive():
            hp = self.dir / 'rows' / f'{process}.csv'
            n = 0
            if hp.exists():
                n = sum(1 for _ in open(hp, encoding='utf-8')) - 1
                hp.unlink()
            known = process in self.M.procs
            if known:
                self.M.reset_process(process)
                self.M.save(self.path)
            return {'process': process, 'rows_deleted': max(n, 0), 'model_reset': known}
