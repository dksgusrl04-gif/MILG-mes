"""Tenant-local, explicit model routing. Registration is an administrative action.
MCT bundles consume already calibrated model slots, not raw sensor columns.
"""
import argparse
import hashlib
import json
import re
import shutil
import uuid
from pathlib import Path
import numpy as np
from .model import CumulativeModel

def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

class Router:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.registry = self.directory/'routes.json'

    def routes(self):
        return json.loads(self.registry.read_text(encoding='utf-8')) if self.registry.exists() else {}

    def register(self, process, model_path, kind, approved_by, preprocessing=None):
        if not re.fullmatch(r'[a-z][a-z0-9_]{1,31}',process) or not approved_by.strip():
            raise ValueError('Valid process and approver required')
        source = Path(model_path)
        if kind=="scenario":
            from .scenario_runtime import register
            return register(self,process,source,approved_by)
        if kind in ('specialist','joint','joint_v2'):
            from .adapters import register_external
            return register_external(self,process,source,kind,approved_by)
        d = json.loads(source.read_text(encoding='utf-8'))
        if kind == 'mct':
            selected = 'model'
            model = CumulativeModel.from_dict(d[selected])
            if process not in model.procs: raise ValueError('Process absent in MCT bundle')
        elif kind == 'causalman':
            selected = None
            model = CumulativeModel.from_dict(d)
            if process != 'causalman_pressfit' or process not in model.procs:
                raise ValueError('Synthetic route must be explicitly named causalman_pressfit')
            if not preprocessing: raise ValueError('Preprocessing recipe required')
            prep = json.loads(Path(preprocessing).read_text(encoding='utf-8'))
            if prep.get('columns') != model.slots or prep.get('synthetic') is not True:
                raise ValueError('Preprocessing does not match synthetic model')
            if any(not np.isfinite(prep['mean'][c]) or not np.isfinite(prep['std'][c]) or prep['std'][c] <= 0 for c in model.slots):
                raise ValueError('Invalid preprocessing statistics')
        else: raise ValueError('Unsupported adapter')
        # Immutable copied bundle; later training cannot silently change this route.
        bundle = self.directory/'routed_models'/uuid.uuid4().hex
        bundle.mkdir(parents=True)
        shutil.copyfile(source,bundle/'model.json')
        if preprocessing: shutil.copyfile(preprocessing,bundle/'preprocessing.json')
        entry = {'kind':kind,'approved_by':approved_by,'selected':selected,
                 'model':(bundle/'model.json').relative_to(self.directory).as_posix(),
                 'sha256':digest(bundle/'model.json'),
                 'input_contract':'calibrated_slots' if kind == 'mct' else 'raw_scalar_measurements',
                 'synthetic':kind == 'causalman'}
        if preprocessing:
            entry['preprocessing'] = (bundle/'preprocessing.json').relative_to(self.directory).as_posix()
            entry['preprocessing_sha256'] = digest(bundle/'preprocessing.json')
        routes = self.routes()
        if self.registry.exists(): shutil.copyfile(self.registry,self.directory/'routes.previous.json')
        routes[process] = entry
        tmp = self.registry.with_suffix('.tmp')
        tmp.write_text(json.dumps(routes,indent=2),encoding='utf-8'); tmp.replace(self.registry)
        return entry

    def rollback(self):
        previous = self.directory/'routes.previous.json'
        if not previous.exists(): raise ValueError('No previous routing registry')
        routes = json.loads(previous.read_text(encoding='utf-8'))
        for entry in routes.values():
            if digest(self.directory/entry['model']) != entry['sha256']:
                raise ValueError('Previous model checksum mismatch')
            if 'preprocessing' in entry and digest(self.directory/entry['preprocessing']) != entry['preprocessing_sha256']:
                raise ValueError('Previous preprocessing checksum mismatch')
        tmp = self.registry.with_suffix('.tmp')
        tmp.write_text(json.dumps(routes,indent=2),encoding='utf-8'); tmp.replace(self.registry)

    def predict(self, process, df):
        route = self.routes().get(process)
        if route is None: return None
        if route['kind']=='scenario':
            from .scenario_runtime import predict
            return predict(self,process,route,df)
        if route['kind'] in ('specialist','joint','joint_v2'):
            from .adapters import predict_external
            return predict_external(self,process,route,df)
        path = self.directory/route['model']
        if digest(path) != route['sha256']: raise ValueError('Registered model checksum mismatch')
        data = json.loads(path.read_text(encoding='utf-8'))
        model = CumulativeModel.from_dict(data[route['selected']] if route['selected'] else data)
        if route['kind'] == 'mct':
            if len(df.columns) != model.K or set(df.columns) != set(model.slots):
                raise ValueError('MCT route requires exact calibrated feature slots; raw sensor input needs its calibrated adapter')
            X = df[model.slots].to_numpy(dtype=float)
        else:
            pp = self.directory/route['preprocessing']
            if digest(pp) != route['preprocessing_sha256']: raise ValueError('Preprocessing checksum mismatch')
            prep = json.loads(pp.read_text(encoding='utf-8'))
            missing = set(prep['raw_columns'])-set(df.columns)
            if missing: raise ValueError('Missing measurement columns: '+str(sorted(missing)))
            work = df[prep['raw_columns']].astype(float).copy()
            for c in prep['columns']:
                if c.endswith('_relative_to_tolerance'):
                    base = c.removesuffix('_relative_to_tolerance')
                    lo,hi = base+'_LTL',base+'_UTL'
                    if lo not in df or hi not in df: raise ValueError('Tolerance bounds missing for '+base)
                    lower,upper = df[lo].astype(float),df[hi].astype(float)
                    if (upper <= lower).any(): raise ValueError('Invalid tolerance width')
                    work[c] = (work[base]-(upper+lower)/2)/(upper-lower)
            X = work[prep['columns']].to_numpy(float)
            if not np.isfinite(X).all(): raise ValueError('Nonfinite measurements')
            X = np.clip((X-np.array([prep['mean'][c] for c in model.slots]))/
                        np.array([prep['std'][c] for c in model.slots]),*prep['clip'])
        if not np.isfinite(X).all(): raise ValueError('Nonfinite feature slots')
        mu = model.predict(process,X,np.ones(len(df)))[0]
        p = model.prob(process,mu)
        return {'process':process,'mode':'registered_model','adapter':route['kind'],
                'synthetic':route['synthetic'],'model_sha256':route['sha256'],
                'input_contract':route['input_contract'], 'version':model.version,
                'rows':[{'i':i,'expected':float(v),'risk':float(v),'p':float(p[i]),'top':[]} for i,v in enumerate(mu)]}

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state',required=True); parser.add_argument('--process',required=True)
    parser.add_argument('--model',required=True); parser.add_argument('--kind',choices=['mct','causalman','specialist','joint','joint_v2','scenario'],required=True)
    parser.add_argument('--preprocessing'); parser.add_argument('--approved-by',required=True)
    a = parser.parse_args()
    print(json.dumps(Router(a.state).register(a.process,a.model,a.kind,a.approved_by,a.preprocessing),indent=2))
