"""Versioned process recipes and per-tool, per-installation streaming state.

No arbitrary expressions or scripts are executed from a profile. Recipe outputs
are observations, not causal predictions or automatic machine commands.
"""
from __future__ import annotations
import copy
import hashlib
import json
from pathlib import Path
import numpy as np

SLOTS = ['profile:rms', 'profile:peak', 'profile:slope', 'profile:integral',
         'profile:relative', 'profile:baseline_ready', 'profile:available']
ROOT = Path(__file__).resolve().parent.parent / 'profiles'

def load_profile(name: str) -> dict:
    if name not in ('mct_endmill', 'screw_tightening'):
        raise ValueError('Unknown process template')
    p = json.loads((ROOT / (name + '.json')).read_text(encoding='utf-8'))
    validate(p)
    return p

def validate(p):
    if p.get('schema_version') != 1 or p.get('kind') not in ('mct', 'screw'):
        raise ValueError('Unsupported template schema or process')
    if not p.get('version') or not p.get('id'):
        raise ValueError('Template identity required')
    if p.get('baseline', {}).get('min_samples', 0) < 2:
        raise ValueError('At least two confirmed reference samples required')
    if p.get('claims', {}).get('causal_effects') is not False:
        raise ValueError('Causal effect predictions are not supported')
    if p['baseline'].get('confirmation') != 'operator_confirmed' or p['baseline'].get('freeze') is not True:
        raise ValueError('Only confirmed, frozen reference policy is supported')
    if p.get('kind') == 'screw' and not p.get('stage'):
        raise ValueError('Tightening stage required')
    for x in p.get('engineering_limits', {}).values():
        if len(x) != 2 or not all(np.isfinite(x)) or x[0] >= x[1]:
            raise ValueError('Limits must be finite [lower, upper] bounds')

class ProfileRuntime:
    def __init__(self, profile: dict, tenant: str):
        validate(profile)
        if not tenant:
            raise ValueError('Tenant identity required')
        self.profile = copy.deepcopy(profile)
        self.tenant = tenant
        self.states = {}

    @property
    def fingerprint(self):
        return hashlib.sha256(json.dumps(self.profile, sort_keys=True).encode()).hexdigest()

    def observe(self, row: dict):
        p = self.profile
        identity = ['machine_id', 'tool_id', 'installation_id']
        if any(not str(row.get(k, '')).strip() for k in identity):
            raise ValueError('Machine, physical tool and installation IDs required')
        # Returning to a tool preserves its state. A new installation ID resets it.
        key = json.dumps([row[k] for k in identity] + [row.get(k) for k in p['condition_keys']])
        if any(row.get(k) is None for k in p['condition_keys']):
            raise ValueError('All comparison condition keys must be supplied')
        previous = self.states.get(key, {'count': 0, 'seconds': 0., 'reference': []})
        state = copy.deepcopy(previous)
        result = dict.fromkeys(SLOTS, 0.)
        unavailable = []
        signal = row.get(p['signal'])
        axis = row.get(p['axis'])
        if p['kind'] == 'screw':
            steps = row.get('tightening steps', [])
            step = next((s for s in steps if s.get('name') == p['stage']), None)
            g = step.get('graph', {}) if step else {}
            signal, axis = g.get('torque values'), g.get('angle values')
        if signal is None or axis is None:
            unavailable.append('required_signal_or_stage_missing')
        else:
            y, x = np.asarray(signal, dtype=float), np.asarray(axis, dtype=float)
            if y.ndim != 1 or x.ndim != 1 or len(y) != len(x) or len(y) < 3:
                unavailable.append('invalid_curve_shape')
            elif not np.isfinite(y).all() or not np.isfinite(x).all() or (np.diff(x) <= 0).any():
                unavailable.append('nonfinite_or_nonincreasing_axis')
            else:
                result['profile:rms'] = float(np.sqrt(np.mean(y*y)))
                result['profile:peak'] = float(np.max(np.abs(y)))
                result['profile:slope'] = float(np.polyfit(x-x[0], y, 1)[0])
                result['profile:integral'] = float(np.sum(np.diff(x)*(y[1:]+y[:-1])/2))
        minimum = p['baseline']['min_samples']
        ref = state['reference']
        # Score using previously approved references only, then ingest this sample.
        ready = len(ref) >= minimum
        if ready and not unavailable:
            base = float(np.median(ref))
            result['profile:relative'] = (result['profile:rms']-base)/max(abs(base), 1e-9)
            result['profile:baseline_ready'] = 1.
        if not unavailable:
            result['profile:available'] = 1.
            state['count'] += 1
            seconds = float(row.get('cut_seconds', 0))
            if not np.isfinite(seconds) or seconds < 0:
                raise ValueError('Invalid cutting duration')
            state['seconds'] += seconds
            if row.get('confirmed_normal') is True and len(ref) < minimum:
                ref.append(result['profile:rms'])
            self.states[key] = state
        checks = {k: ('unavailable' if unavailable else
                  ('within_limits' if lo <= result[k] <= hi else 'outside_limits'))
                  for k, (lo, hi) in p['engineering_limits'].items() if k in result}
        return {'features': result, 'available': not unavailable,
                'baseline_status': 'ready' if ready else 'insufficient_confirmed_reference',
                'unavailable': unavailable, 'usage_count': state['count'],
                'cut_seconds': state['seconds'], 'engineering_checks': checks,
                'evaluation_label_rule': p['evaluation_label_rule'],
                'template_id': p['id'], 'template_version': p['version'],
                'template_hash': self.fingerprint, 'causal_effect_prediction': None}

    def save(self, path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + '.tmp')
        tmp.write_text(json.dumps({'tenant': self.tenant, 'profile': self.profile,
                                  'hash': self.fingerprint, 'states': self.states}), encoding='utf-8')
        tmp.replace(path)

    @classmethod
    def load(cls, path, tenant, profile):
        data = json.loads(Path(path).read_text(encoding='utf-8'))
        obj = cls(profile, tenant)
        if data['tenant'] != tenant or data['hash'] != obj.fingerprint:
            raise ValueError('Tenant or template mismatch; migrate and revalidate first')
        obj.states = data['states']
        return obj
