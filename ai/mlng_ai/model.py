"""누적 학습 모델 — 공정 공통 효과 + 공정별 보정, 베이즈 포아송 회귀 (사후분포를 배치마다 갱신)

  log(기대 불량 수) = log(노출) + α_공정 + Σ_슬롯 (β_슬롯 + δ_공정,슬롯) · x_슬롯

  β (공통 효과)   모든 공정이 같이 배우는 규칙 — 예: «온도가 평소보다 1σ 벗어나면 불량 ×1.2»
  δ (공정별 보정) 공정마다 다른 부분. 0 쪽으로 강하게 당겨서(축소), 데이터가 충분할 때만 커진다
  α (공정 기준)   공정마다 평소 불량률

누적 학습 (재학습 없이 갱신)
  지금 사후분포 N(m, S) 를 다음 배치의 사전분포로 쓴다 → 새 배치로 MAP 를 찾고(뉴턴법), 곡률로 새 S 를 만든다 (라플라스 근사)
  망각 계수 λ: 갱신 전에 S ← S/λ  (오래된 데이터의 무게를 조금씩 줄임). 입력 분포가 바뀌면(드리프트) λ 를 낮춰 빨리 적응
  과산포: 공정마다 φ (피어슨 χ²/자유도, 누적) 로 가능도를 나눠 신뢰구간을 정직하게 넓힌다
  공정별 가중: 한 공정(예: 1만 행)이 공통 효과를 독차지하지 않도록 배치마다 공정의 유효 표본을 맞춘다
"""
from __future__ import annotations

import json

import numpy as np


class CumulativeModel:
    def __init__(self, slots: list[str], tau_beta: float = 0.5, tau_delta: float = 0.25, tau_alpha: float = 3.0):
        self.slots = list(slots)
        self.K = len(self.slots)
        self.procs: list[str] = []
        self.tau = (tau_beta, tau_delta, tau_alpha)
        self.m = np.zeros(self.K)
        self.S = np.eye(self.K) * tau_beta ** 2
        self.phi: dict[str, list[float]] = {}        # 공정 → [χ² 합, 자유도 합]
        self.seen: dict[str, float] = {}             # 공정 → 지금까지 본 행 수
        self.acc: dict[str, list[float]] = {}        # 공정 → [Σy, Σn, Σy²/n] (공정 기준율만으로 본 과산포 — 초기 φ)
        self.gate: dict[str, list[float]] = {}       # 공정 → [모델 누적 이탈도, 기준율 누적 이탈도] (예측 후 채점)
        self.q_alpha = 0.02                          # 배치마다 공정 기준율(log)이 변할 수 있는 정도 (분산)
        self.version = 0
        self.history: list[dict] = []

    # ── 매개변수 위치
    def _idx(self, p: str):
        i = self.procs.index(p)
        a = self.K + i * (self.K + 1)
        return a, np.arange(a + 1, a + 1 + self.K)

    def add_process(self, p: str, base_rate: float):
        if p in self.procs:
            return
        self.procs.append(p)
        _, td, ta = self.tau
        add = self.K + 1
        m0 = np.concatenate([[np.log(max(base_rate, 1e-7))], np.zeros(self.K)])
        S0 = np.diag([ta ** 2] + [td ** 2] * self.K)
        n = len(self.m)
        S = np.zeros((n + add, n + add)); S[:n, :n] = self.S; S[n:, n:] = S0
        self.m, self.S = np.concatenate([self.m, m0]), S
        self.phi[p] = [0.0, 0.0]; self.seen[p] = 0.0

    def design(self, p: str, X: np.ndarray) -> np.ndarray:
        a, d = self._idx(p)
        D = np.zeros((len(X), len(self.m)))
        D[:, :self.K] = X
        D[:, a] = 1.0
        D[:, d] = X
        return D

    def dispersion(self, p: str) -> float:
        """예측 잔차로 잰 φ 가 충분히 쌓이면 그것, 아니면 «공정 기준율만» 으로 잰 φ (조금 크게 잡히는 보수적 값)"""
        c, df = self.phi.get(p, [0, 0])
        if df > 30:
            return float(np.clip(c / df, 1.0, 50.0))
        sy, sn, q = self.acc.get(p, [0, 0, 0])
        if sy > 0 and sn > 0:
            r = sy / sn
            return float(np.clip((q / r - sy) / max(self.seen.get(p, 1) - 1, 1), 1.0, 50.0))
        return 1.0

    # ── 예측: 기대 사건 수 · log 율의 표준편차
    def predict(self, p: str, X: np.ndarray, n: np.ndarray):
        D = self.design(p, X)
        eta = D @ self.m
        sd = np.sqrt(np.einsum('ij,jk,ik->i', D, self.S, D).clip(0))
        return n * np.exp(np.clip(eta, -30, 20)), sd

    # ── 신뢰 게이트: 이 공정에서 모델이 «공정 기준율» 보다 실제로 나았는가 (지금까지의 예측 후 채점)
    def base_rate(self, p: str) -> float:
        sy, sn, _ = self.acc.get(p, [0, 0, 0])
        return (sy + 0.5) / (sn + 1)

    def gate_weight(self, p: str) -> float:
        dm, db = self.gate.get(p, [0.0, 0.0])
        eta = 0.5 / self.dispersion(p)
        z = np.clip(-eta * (dm - db), -50, 50)
        return float(1 / (1 + np.exp(-z)))          # 모델이 나았으면 1 쪽, 못했으면 0 쪽

    def predict_gated(self, p: str, X: np.ndarray, n: np.ndarray):
        mu, sd = self.predict(p, X, n)
        w = self.gate_weight(p)
        return w * mu + (1 - w) * self.base_rate(p) * n, sd, w

    @staticmethod
    def _dev(y, mu):
        mu = np.maximum(mu, 1e-12)
        return float(2 * np.sum(np.where(y > 0, y * np.log(np.where(y > 0, y, 1) / mu), 0.0) - (y - mu)))

    # ── 누적 갱신 (한 배치 · 여러 공정 가능)
    def update(self, batches: list[tuple[str, np.ndarray, np.ndarray, np.ndarray]], lam: float = 0.995, weight_cap: float = 400.0, iters: int = 25):
        if not batches:
            return
        for p, X, y, n in batches:            # 갱신 전 예측으로 과산포 φ 누적 (예측 후 학습)
            if self.seen.get(p, 0) > 0:
                mu, _ = self.predict(p, X, n)
                self.phi[p][0] += float(np.sum((y - mu) ** 2 / np.maximum(mu, 1e-9)))
                self.phi[p][1] += len(y)
                g = self.gate.setdefault(p, [0.0, 0.0])
                g[0] += self._dev(y, mu); g[1] += self._dev(y, self.base_rate(p) * n)
        S0 = self.S / lam
        for p, *_ in batches:                 # 공정 기준율은 천천히 움직일 수 있다 (상태 공간: α_t = α_t-1 + 잡음)
            if p in self.procs:
                a, _ = self._idx(p); S0[a, a] += self.q_alpha
        P0 = np.linalg.inv(S0)
        m0 = self.m.copy()
        Ds, ys, offs, ws = [], [], [], []
        for p, X, y, n in batches:
            seen = self.seen.get(p, 0)
            a = self.acc.setdefault(p, [0.0, 0.0, 0.0]); a[0] += float(y.sum()); a[1] += float(n.sum()); a[2] += float(np.sum(y * y / n))
            self.seen[p] = seen + len(y)
            w = min(1.0, weight_cap / max(len(y), 1)) / self.dispersion(p)
            Ds.append(self.design(p, X)); ys.append(y); offs.append(np.log(n)); ws.append(np.full(len(y), w))
        D, y, off, w = np.vstack(Ds), np.concatenate(ys), np.concatenate(offs), np.concatenate(ws)
        th = m0.copy()
        for _ in range(iters):
            eta = np.clip(off + D @ th, -30, 20)
            mu = np.exp(eta)
            g = D.T @ (w * (y - mu)) - P0 @ (th - m0)
            H = (D * (w * mu)[:, None]).T @ D + P0
            step = np.linalg.solve(H, g)
            th = th + step
            if np.max(np.abs(step)) < 1e-6:
                break
        mu = np.exp(np.clip(off + D @ th, -30, 20))
        H = (D * (w * mu)[:, None]).T @ D + P0
        self.m, self.S = th, np.linalg.inv(H)
        self.S = (self.S + self.S.T) / 2
        self.version += 1

    # ── 해석: 공통 효과 (배수와 90% 구간)
    def shared_effects(self):
        sd = np.sqrt(np.diag(self.S)[:self.K])
        b = self.m[:self.K]
        return [{'slot': s, 'beta': float(b[i]), 'rr': float(np.exp(b[i])), 'lo': float(np.exp(b[i] - 1.645 * sd[i])), 'hi': float(np.exp(b[i] + 1.645 * sd[i]))} for i, s in enumerate(self.slots)]

    def process_effects(self, p: str):
        a, d = self._idx(p)
        idx = np.arange(self.K)
        tot = self.m[idx] + self.m[d]
        var = np.diag(self.S)[idx] + np.diag(self.S)[d] + 2 * self.S[idx, d]
        sd = np.sqrt(np.clip(var, 0, None))
        return {'base_rate': float(np.exp(self.m[a])), 'effects': [{'slot': s, 'beta': float(tot[i]), 'rr': float(np.exp(tot[i])), 'lo': float(np.exp(tot[i] - 1.645 * sd[i])), 'hi': float(np.exp(tot[i] + 1.645 * sd[i]))} for i, s in enumerate(self.slots)]}

    def explain(self, p: str, x: np.ndarray):
        """한 행: 슬롯마다 log 율 기여 (β+δ)·x 와 90% 구간 — 클수록 «이 LOT 불량을 키운 쪽»"""
        _, d = self._idx(p)
        idx = np.arange(self.K)
        out = []
        for i, s in enumerate(self.slots):
            if x[i] == 0:
                continue
            v = np.array([i, d[i]])
            c = (self.m[i] + self.m[d[i]]) * x[i]
            sd = abs(x[i]) * np.sqrt(max(self.S[np.ix_(v, v)].sum(), 0))
            out.append({'slot': s, 'x': float(x[i]), 'contrib': float(c), 'lo': float(c - 1.645 * sd), 'hi': float(c + 1.645 * sd)})
        return sorted(out, key=lambda r: -r['contrib'])

    # ── 저장 · 불러오기 (서버가 누적 상태를 이어 간다)
    def to_dict(self, full: bool = True):
        d = {'slots': self.slots, 'procs': self.procs, 'tau': self.tau, 'm': self.m.tolist(), 'phi': self.phi, 'seen': self.seen, 'acc': self.acc, 'gate': self.gate, 'q_alpha': self.q_alpha, 'version': self.version, 'history': self.history}
        d['S'] = self.S.tolist() if full else np.diag(self.S).tolist()
        return d

    @classmethod
    def from_dict(cls, d: dict):
        o = cls(d['slots'], *d['tau'])
        o.procs, o.m, o.S = list(d['procs']), np.array(d['m']), np.array(d['S'])
        o.phi, o.seen, o.version, o.history, o.acc = d['phi'], d['seen'], d['version'], d.get('history', []), d.get('acc', {})
        o.gate, o.q_alpha = d.get('gate', {}), d.get('q_alpha', 0.02)
        return o

    def save(self, path):
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(self.to_dict(), f)

    @classmethod
    def load(cls, path):
        with open(path, encoding='utf-8') as f:
            return cls.from_dict(json.load(f))
