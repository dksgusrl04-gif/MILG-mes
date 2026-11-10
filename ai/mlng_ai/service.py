"""서버용 서비스 — FastAPI(server/api) 가 부른다. 웹 프레임워크와 무관한 순수 Python.

  map(df, meta)                   처음 보는 표의 칼럼 해석
  update(process, df, meta)       새 데이터로 누적 학습 1회: 먼저 예측해 채점 → 그다음 학습 → 상태 저장
  predict(process, df, meta)      학습 없이 위험도만 — 행마다 기대 불량 · 확률 · 키운 쪽(슬롯 · 칼럼)
  diagnose(df, meta, unit_cost)   데이터 진단 리포트 (칼럼 해석 · 품질 · 원인 후보 · 손실)
상태: <state_dir>/model.json (누적 모델) · <state_dir>/rows/<공정>.csv (공정마다 받은 원본 — «평소 값» 계산용)

공장별 모델 운영 (운영 모델은 바로 바꾸지 않는다)
  ingest(process, df)             데이터만 쌓는다 (학습 안 함)
  train_candidate(process)        후보 모델 학습: 앞 기간으로 학습 → 학습에 안 쓴 이후 기간으로 운영 모델과 비교
  promote(version, by)            담당자 승인 → 운영 모델 교체 (이전 버전 보관)
  rollback(by)                    직전 운영 모델로 복구
  registry()                      버전 · 평가 · 승인 · 복구 기록
  상태는 이 공장(테넌트) 폴더 안에만 있고, 외부로 보내는 경로가 없다. 다른 공장 모델과 공유하지 않는다.
"""
from __future__ import annotations

import datetime as dt
try:
    import fcntl
except ImportError:  # Windows local engine execution
    fcntl = None
    import msvcrt
import json
import shutil
import re
import threading
import uuid
from contextlib import contextmanager
from pathlib import Path

import numpy as np
import pandas as pd

from .datasets import Table
from .engine import poisson_dev
from .features import SLOT_KO, SLOTS, encode, standardize
from .model import CumulativeModel
from .schema import sense_table
from .routing import Router
from .process_context import analyze, recognize, choose_registered_process, family

KEY = re.compile(r'^[a-z][a-z0-9_]{1,31}$')


class Service:
    def __init__(self, state_dir: str | Path, seed_model: str | Path | None = None):
        self.dir = Path(state_dir)
        (self.dir / 'rows').mkdir(parents=True, exist_ok=True)
        self.path = self.dir / 'model.json'
        self.router = Router(self.dir)
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
        with self.lock, open(self.dir / '.lock', 'a+b') as lf:
            if fcntl is not None:
                fcntl.flock(lf, fcntl.LOCK_EX)
            else:
                lf.seek(0, 2)
                if lf.tell() == 0:
                    lf.write(b'0'); lf.flush()
                lf.seek(0); msvcrt.locking(lf.fileno(), msvcrt.LK_LOCK, 1)
            try:
                if self.path.exists():
                    self.M = CumulativeModel.load(self.path)
                yield
            finally:
                if fcntl is not None:
                    fcntl.flock(lf, fcntl.LOCK_UN)
                else:
                    lf.seek(0); msvcrt.locking(lf.fileno(), msvcrt.LK_UNLCK, 1)

    def status(self) -> dict:
        if self.path.exists():
            self.M = CumulativeModel.load(self.path)
        pilot = None
        if (self.dir / 'pilot-active.json').exists():
            from .pilot import PilotEngine
            pilot = PilotEngine(self.dir).status()
        return {'version': self.M.version, 'pilot': pilot, 'model_routes': self.router.routes(), 'processes': [{'key': p, 'rows': self.M.seen.get(p, 0), 'gate': round(self.M.gate_weight(p), 3),
                                                          'base_rate': self.M.base_rate(p)} for p in self.M.procs]}

    @staticmethod
    def map(df: pd.DataFrame, meta: dict | None = None) -> list[dict]:
        return [s.to_dict() for s in sense_table(df, meta or {}).values()]

    def update(self, process: str, df: pd.DataFrame, meta: dict | None = None, time_col: str | None = None) -> dict:
        self._check_pilot_training(process)
        if not KEY.match(process):
            raise ValueError('공정 키는 영문 소문자 · 숫자 · _ (2~32자)')
        if len(df) == 0:
            raise ValueError('행이 없습니다')
        if process in self.router.routes():
            raise ValueError('Registered specialist model: train a separate candidate and register its validated bundle')
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

    def analyze_process(self, df: pd.DataFrame, process: str = '', template_id: str | None = None) -> dict:
        if len(df) == 0:
            raise ValueError('No rows supplied')
        result = analyze(df, process, template_id)
        result['registered_candidates'] = [
            {'process':k,'input_contract':r['input_contract'],'synthetic':r['synthetic']}
            for k,r in self.router.routes().items()
            if k == process or (result['recognition']['family'] and
                family(k) == result['recognition']['family'])]
        return result

    def predict(self, process: str, df: pd.DataFrame, meta: dict | None = None, top: int = 3, template_id: str | None = None) -> dict:
        if not KEY.match(process):
            raise ValueError('공정 키는 영문 소문자 · 숫자 · _ (2~32자)')
        if len(df) == 0:
            raise ValueError('행이 없습니다')
        if (self.dir / 'pilot-active.json').exists() and template_id is None:
            from .pilot import PilotEngine
            pilot = PilotEngine(self.dir)
            if process == 'auto' or process in pilot.manifest['processes']:
                result = pilot.predict(process, df.to_dict('records'), (meta or {}).get('context'))
                if process != 'auto' or result.get('mode') != 'needs_confirmation':
                    return result
        context = recognize(df, '' if process == 'auto' else process)
        if context['status'] in ('conflict', 'needs_confirmation'):
            return {'mode':'needs_confirmation','recognition':context,'rows':[]}
        if process == 'auto':
            chosen = choose_registered_process(context, self.router.routes(), df)
            if chosen is None:
                return self.analyze_process(df, '', template_id)
            process = chosen
        if template_id:
            preview = self.analyze_process(df, process, template_id)
            # Existing weights were not trained with this requested recipe.
            # Return calculations without silently changing the model's inputs.
            return preview
        if self.path.exists():
            self.M = CumulativeModel.load(self.path)
        routed = self.router.predict(process, df)
        if routed is not None:
            return routed
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

    # ── 공장별 모델 운영: 후보 → 검증 → 승인 → 복구
    def _reg_file(self) -> Path:
        return self.dir / 'registry.json'

    def registry(self) -> dict:
        p = self._reg_file()
        if p.exists():
            return json.loads(p.read_text(encoding='utf-8'))
        return {'active': 'base', 'versions': {'base': {'kind': 'base', 'created': None, 'note': '공정별 기본 모델 (설치 시 상태)'}}, 'history': []}

    def _save_reg(self, reg: dict) -> None:
        tmp = self._reg_file().with_suffix('.tmp')
        tmp.write_text(json.dumps(reg, ensure_ascii=False, indent=1, default=float), encoding='utf-8')
        tmp.replace(self._reg_file())

    def ingest(self, process: str, df: pd.DataFrame, meta: dict | None = None) -> dict:
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
            allr.to_csv(hp, index=False)
            return {'process': process, 'added': int(len(df)), 'rows': int(len(allr))}

    def workflow(self, process: str, df: pd.DataFrame, meta: dict | None = None,
                 template_id: str | None = None, save_input: bool = False) -> dict:
        """Receive → recognize/template → infer → persist a sensor-free review event."""
        if len(df) == 0:
            raise ValueError('행이 없습니다')
        if not isinstance(save_input, bool):
            raise ValueError('save_input은 true/false여야 합니다')
        recognition = recognize(df, '' if process == 'auto' else process)
        prediction = self.predict(process, df, meta, template_id=template_id)
        event_id = uuid.uuid4().hex
        now = dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')
        if save_input:
            selected = prediction.get('process') or process
            if selected == 'auto' or not KEY.match(selected):
                raise ValueError('공정이 확정된 경우에만 원본 입력을 적재할 수 있습니다')
            ingest_result = self.ingest(selected, df, meta)
        else:
            ingest_result = None
        event = {'event_id': event_id, 'created_at': now,
                 'process': prediction.get('process') or process,
                 'recognition': recognition,
                 'template': prediction.get('template'),
                 'prediction': prediction,
                 'input_rows': int(len(df)),
                 'input_sha256': __import__('hashlib').sha256(
                     df.to_json(orient='records', date_format='iso', force_ascii=False).encode('utf-8')).hexdigest(),
                 'raw_input_saved': bool(save_input)}
        events = self.dir / 'events'
        events.mkdir(parents=True, exist_ok=True)
        with self._exclusive():
            (events / f'{event_id}.json').write_text(
                json.dumps(event, ensure_ascii=False, default=float), encoding='utf-8')
        return {**event, 'ingest': ingest_result}

    def record_feedback(self, event_id: str, action: str, actor: str,
                        outcome: str = 'unknown', note: str = '') -> dict:
        """Append an operator disposition without changing model weights."""
        if not re.fullmatch(r'[a-f0-9]{32}', event_id):
            raise ValueError('유효하지 않은 event_id')
        if action not in {'investigate', 'defer', 'accept', 'dismiss'}:
            raise ValueError('action은 investigate/defer/accept/dismiss 중 하나여야 합니다')
        if outcome not in {'unknown', 'pass', 'fail'}:
            raise ValueError('outcome은 unknown/pass/fail 중 하나여야 합니다')
        actor = str(actor).strip()
        if not actor or len(actor) > 80 or len(note) > 1000:
            raise ValueError('담당자(1~80자)와 메모(최대 1000자)를 확인하세요')
        event_path = self.dir / 'events' / f'{event_id}.json'
        if not event_path.is_file():
            raise ValueError('이 테넌트에서 찾을 수 없는 추론 event_id입니다')
        record = {'feedback_id': uuid.uuid4().hex, 'event_id': event_id,
                  'created_at': dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds'),
                  'action': action, 'outcome': outcome, 'actor': actor, 'note': note.strip()}
        with self._exclusive():
            with open(self.dir / 'feedback.jsonl', 'a', encoding='utf-8') as f:
                f.write(json.dumps(record, ensure_ascii=False) + '\n')
        return record

    def train_candidate(self, process: str, meta: dict | None = None, holdout: float = 0.3, budget: float = 0.1) -> dict:
        self._check_pilot_training(process)
        from .evaluate import alert_metrics, rank_quality, score_model, skill
        if not KEY.match(process):
            raise ValueError('공정 키는 영문 소문자 · 숫자 · _ (2~32자)')
        hp = self.dir / 'rows' / f'{process}.csv'
        if not hp.exists():
            raise ValueError('쌓인 데이터가 없습니다 — 먼저 ingest')
        with self._exclusive():
            allr = pd.read_csv(hp)
            if len(allr) < 40:
                raise ValueError('후보 학습에는 40행 이상 필요합니다')
            e = encode(Table(process, process, allr, meta or {}, {}, '', {}, 'row'))
            Xs = standardize(e.X.reindex(columns=SLOTS, fill_value=0.0))[SLOTS].to_numpy()     # 이전 행 기준 표준화 — 미래 정보 없음
            cut = int(len(allr) * (1 - holdout))
            tr, te = np.arange(cut), np.arange(cut, len(allr))
            if e.y[te].sum() == 0:
                raise ValueError('평가 구간(이후 기간)에 불량이 없어 비교할 수 없습니다')
            cand = CumulativeModel.from_dict(self.M.to_dict())
            cand.reset_process(process)
            cand.add_process(process, (e.y[tr].sum() + 0.5) / (e.n[tr].sum() + 1))
            for ix in np.array_split(tr, max(1, min(10, len(tr) // 20))):
                cand.update([(process, Xs[ix], e.y[ix], e.n[ix])])

            def ev(M):
                if process not in M.procs:
                    return None
                sc = score_model(M, process, Xs[te], e.n[te])
                r = alert_metrics(sc, e.y[te], e.n[te], budget)
                r['rank'] = rank_quality(sc, e.y[te], e.n[te])
                r['skill'] = skill(M, process, Xs[te], e.y[te], e.n[te])
                return r
            cur, new = ev(self.M), ev(cand)
            better = cur is None or ((new['capture'] or 0) >= (cur['capture'] or 0) and (new['skill'] or 0) >= (cur['skill'] or 0) - 0.01)
            reg = self.registry()
            vid = 'v' + dt.datetime.now().strftime('%Y%m%d%H%M%S%f')[:17]
            (self.dir / 'versions').mkdir(exist_ok=True)
            cand.save(self.dir / 'versions' / f'{vid}.json')
            reg['versions'][vid] = {'kind': 'candidate', 'process': process, 'created': dt.datetime.now().isoformat(timespec='seconds'),
                                    'train_rows': int(len(tr)), 'eval_rows': int(len(te)), 'eval_events': float(e.y[te].sum()),
                                    'eval': {'active': cur, 'candidate': new}, 'recommend': 'promote' if better else 'keep', 'status': 'candidate'}
            self._save_reg(reg)
            return json.loads(json.dumps({'version': vid, **reg['versions'][vid]}, default=float))

    def _check_pilot_training(self, process):
        if (self.dir / 'pilot-active.json').exists():
            from .pilot import PilotEngine
            if process in PilotEngine(self.dir).manifest['processes']:
                raise ValueError('Pilot route: use the versioned pilot builder; press-fit references use pilot calibrate with inspected cycles')

    def promote(self, version: str, by: str = '') -> dict:
        with self._exclusive():
            reg = self.registry()
            v = reg['versions'].get(version)
            if not v or v.get('kind') != 'candidate':
                raise ValueError('승인할 후보 버전이 없습니다')
            src = self.dir / 'versions' / f'{version}.json'
            prev = reg['active']
            if prev == 'base' and self.path.exists() and not (self.dir / 'versions' / 'base.json').exists():
                (self.dir / 'versions').mkdir(exist_ok=True)
                shutil.copy(self.path, self.dir / 'versions' / 'base.json')
            elif prev == 'base' and not self.path.exists():
                self.M.save(self.dir / 'versions' / 'base.json')
            shutil.copy(src, self.path)
            self.M = CumulativeModel.load(self.path)
            v['status'] = 'active'
            if prev in reg['versions'] and prev != 'base':
                reg['versions'][prev]['status'] = 'retired'
            reg['active'] = version
            reg['history'].append({'at': dt.datetime.now().isoformat(timespec='seconds'), 'action': 'promote', 'from': prev, 'to': version, 'by': by})
            self._save_reg(reg)
            return {'active': version, 'previous': prev}

    def rollback(self, by: str = '') -> dict:
        with self._exclusive():
            reg = self.registry()
            prevs = [h['from'] for h in reg['history'] if h['action'] == 'promote' and h['to'] == reg['active']]
            if not prevs:
                raise ValueError('되돌릴 이전 버전이 없습니다')
            target = prevs[-1]
            src = self.dir / 'versions' / f'{target}.json'
            if not src.exists():
                raise ValueError(f'이전 버전 파일이 없습니다: {target}')
            shutil.copy(src, self.path)
            self.M = CumulativeModel.load(self.path)
            cur = reg['active']
            if cur in reg['versions']:
                reg['versions'][cur]['status'] = 'rolled_back'
            if target in reg['versions'] and target != 'base':
                reg['versions'][target]['status'] = 'active'
            reg['active'] = target
            reg['history'].append({'at': dt.datetime.now().isoformat(timespec='seconds'), 'action': 'rollback', 'from': cur, 'to': target, 'by': by})
            self._save_reg(reg)
            return {'active': target, 'previous': cur}
