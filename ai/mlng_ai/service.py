"""서버용 서비스 — FastAPI(server/api) 가 부른다. 웹 프레임워크와 무관한 순수 Python.

  map(df, meta)                   처음 보는 표의 칼럼 해석
  update(process, df, meta)       새 데이터로 누적 학습 1회: 먼저 예측해 채점 → 그다음 학습 → 상태 저장
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
from .features import SLOTS, encode, standardize
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
