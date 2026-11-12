"""Portable, checked local delivery and isolated acceptance. No training or field claims.

Only load bundles built from trusted local models. Checksums detect corruption, not
the authenticity of an untrusted sender. Portable snapshots never include live logs,
inspection data, API tokens or tenant-specific models.
"""
import argparse
import copy
import csv
import hashlib
import html
import importlib.metadata
import json
import platform
import shutil
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import time
import uuid
import zipfile
from datetime import datetime, timezone
from contextlib import closing
from pathlib import Path

from .pilot import PilotEngine, write_json
from .routing import digest


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def inside(root, relative):
    root = Path(root).resolve()
    path = (root / relative).resolve()
    if path == root or not path.is_relative_to(root):
        raise ValueError('Artifact path escapes its release')
    return path


def inspect_release(state):
    """Check all hashes before any joblib/model deserialization."""
    pointer = read(Path(state)/'pilot-active.json')
    release = Path(pointer['release']).resolve()
    if digest(release/'release.json') != pointer['sha256']:
        raise ValueError('Release manifest checksum mismatch')
    manifest = read(release/'release.json')
    if digest(release/'routes.json') != manifest['routes_sha256']:
        raise ValueError('Route registry checksum mismatch')
    routes = read(release/'routes.json')
    if set(routes)-set(manifest['processes']):
        raise ValueError('Routes absent from release capabilities')
    files = {'release.json', 'routes.json'}
    for process, route in routes.items():
        for key in ('model', 'preprocessing'):
            if key in route:
                path = inside(release, route[key])
                if digest(path) != route['sha256' if key == 'model' else 'preprocessing_sha256']:
                    raise ValueError('Model checksum mismatch: '+process)
                files.add(route[key])
    for process in manifest['processes']:
        example = inside(release, 'examples/'+process+'.json')
        if not example.is_file():
            raise ValueError('Missing example: '+process)
        sample = read(example)
        if sample.get('process') != process or len(sample.get('rows', [])) != 1:
            raise ValueError('Invalid example contract: '+process)
        files.add('examples/'+process+'.json')
    return release, manifest, routes, sorted(files)


def snapshot(state, destination):
    """A clean portable public-model snapshot, without tenant state."""
    release, manifest, routes, files = inspect_release(state)
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=False)
    for name in files:
        target = inside(destination, name)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(inside(release, name), target)
    # Record authority and provenance in the shipped copy; original stays unchanged.
    manifest = copy.deepcopy(manifest)
    for capability in manifest['processes'].values():
        capability['field_validated'] = False
        capability['quality_release_authority'] = False
    write_json(destination/'release.json', manifest)
    write_json(destination/'snapshot.json', {
        'format': 'milg_clean_snapshot_v1', 'source_release': manifest['id'],
        'contains_tenant_data': False, 'files': {p: digest(destination/p) for p in files}})
    return destination


def install_snapshot(source, state):
    """Only install into a NEW state directory; never replace active customer state."""
    source, state = Path(source).resolve(), Path(state).resolve()
    spec = read(source/'snapshot.json')
    if spec.get('format') != 'milg_clean_snapshot_v1':
        raise ValueError('Unsupported snapshot')
    for name, checksum in spec['files'].items():
        if digest(inside(source, name)) != checksum:
            raise ValueError('Snapshot checksum mismatch: '+name)
    if state.exists():
        raise ValueError('Destination state must not exist; existing state is retained')
    release = state/'pilot-releases'/'portable'
    release.mkdir(parents=True)
    for name in spec['files']:
        dest = inside(release, name); dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(inside(source, name), dest)
    write_json(state/'pilot-active.json', {'release': str(release), 'sha256': digest(release/'release.json')})
    inspect_release(state)
    return state


def backup(state, destination):
    """Consistent SQLite backup plus exact active immutable release and field models.

    A backup contains PRIVATE data; keep it in the tenant's controlled storage.
    The API token is deliberately not copied; restored servers generate a new one.
    """
    state, destination = Path(state).resolve(), Path(destination).resolve()
    release, manifest, routes, files = inspect_release(state)
    if destination.exists():
        raise ValueError('Backup destination already exists')
    destination.mkdir(parents=True)
    for name in files:
        dest = destination/'release'/name; dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(inside(release, name), dest)
    database = state/'pilot.sqlite3'
    field_paths = []
    if database.exists():
        with closing(sqlite3.connect(f'file:{database.as_posix()}?mode=ro', uri=True)) as src:
            with closing(sqlite3.connect(destination/'pilot.sqlite3')) as dst:
                src.backup(dst)
        with closing(sqlite3.connect(destination/'pilot.sqlite3')) as db:
            if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise ValueError('Database integrity check failed')
            field_paths = db.execute('SELECT path,sha256 FROM field_models').fetchall()
        for relative, checksum in field_paths:
            src = inside(state, relative)
            if digest(src) != checksum: raise ValueError('Field model checksum mismatch')
            dest = inside(destination/'field-state', relative); dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dest)
    # Include complete inbox deliveries that may not yet have reached SQLite.
    for pending in (state/'inbox').glob('*.ready.json'):
        if pending.is_symlink() or not pending.resolve().is_relative_to((state/'inbox').resolve()): continue
        dest=destination/'inbox'/pending.name;dest.parent.mkdir(exist_ok=True)
        shutil.copyfile(pending,dest)
    checksums = {p.relative_to(destination).as_posix():digest(p) for p in destination.rglob('*') if p.is_file()}
    write_json(destination/'backup.json', {'format':'milg_private_backup_v1', 'private':True,
        'created':datetime.now(timezone.utc).isoformat(), 'release_id':manifest['id'], 'files':checksums})
    return destination


def restore(source, state):
    source, state = Path(source).resolve(), Path(state).resolve()
    spec = read(source/'backup.json')
    if spec.get('format') != 'milg_private_backup_v1': raise ValueError('Unsupported backup')
    for name, checksum in spec['files'].items():
        if digest(inside(source, name)) != checksum: raise ValueError('Backup checksum mismatch: '+name)
    if state.exists(): raise ValueError('Restore destination must not exist')
    state.mkdir(parents=True)
    release = state/'pilot-releases'/'restored'; release.mkdir(parents=True)
    for name in spec['files']:
        src = inside(source, name)
        if name.startswith('release/'):
            dest = inside(release, name[len('release/'):])
        elif name.startswith('field-state/'):
            dest = inside(state, name[len('field-state/'):])
        elif name == 'pilot.sqlite3': dest = state/name
        elif name.startswith('inbox/'): dest = inside(state,name)
        else: raise ValueError('Unsupported backup member')
        dest.parent.mkdir(parents=True, exist_ok=True); shutil.copyfile(src,dest)
    write_json(state/'pilot-active.json', {'release':str(release), 'sha256':digest(release/'release.json')})
    inspect_release(state)
    # Recovery must not resume learning in parallel with the original deployment.
    with closing(sqlite3.connect(state/'pilot.sqlite3')) as db:
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='learning_policies'").fetchone():
            for scope,process,raw in db.execute('SELECT scope,process,config FROM learning_policies').fetchall():
                config=json.loads(raw);config['enabled']=False
                db.execute('UPDATE learning_policies SET config=? WHERE scope=? AND process=?',
                           (json.dumps(config),scope,process))
            db.commit()
    return state


def acceptance(state, out, repeats=5):
    """Replay EVERY registered example in isolation, without modifying source state."""
    release, manifest, routes, files = inspect_release(state)
    out = Path(out); out.mkdir(parents=True, exist_ok=True)
    report = {'scope':'local_runtime_acceptance_not_quality_validation', 'field_validated':False,
              'source_release':manifest['id'], 'processes':[], 'checks':{}, 'created':datetime.now(timezone.utc).isoformat()}
    with tempfile.TemporaryDirectory(prefix='milg-acceptance-') as temp:
        root = Path(temp)
        snapshot(state, root/'snapshot'); install_snapshot(root/'snapshot',root/'A')
        install_snapshot(root/'snapshot',root/'B')
        engine = PilotEngine(root/'A'); other = PilotEngine(root/'B')
        first = None
        for process in manifest['processes']:
            item = {'process':process, 'runtime_ok':False, 'quality_validated':False}
            try:
                payload = read(release/'examples'/f'{process}.json')
                payload.update(demo=True,cycle_id='acceptance-'+process)
                times=[]
                for _ in range(max(1,repeats)):
                    start=time.perf_counter(); result=engine.predict(process,payload['rows'],payload['context'])
                    times.append((time.perf_counter()-start)*1000)
                saved=engine.submit(payload)
                assert engine.submit(payload)==saved
                restarted=PilotEngine(root/'A'); assert restarted.submit(payload)==saved
                assert all(row.get('quality_prediction') is None for row in result['rows'])
                item.update(runtime_ok=True, mode=result['mode'],
                    median_ms=round(statistics.median(times),2), max_ms=round(max(times),2),
                    input_contract=result.get('input_contract'), target=result.get('target'))
                if first is None: first=(payload,saved)
            except Exception as exc:
                item['error']=str(exc)
            report['processes'].append(item)
        report['checks']['all_routes_replay']=all(p['runtime_ok'] for p in report['processes'])
        report['checks']['tenant_isolation']=other.status()['counts']['events']==0
        if first:
            payload,saved=first
            changed=copy.deepcopy(payload);changed['lot_id']='conflicting-lot'
            try: engine.submit(changed); rejected=False
            except ValueError: rejected=True
            report['checks']['conflicting_cycle_rejected']=rejected
            engine.review(payload['cycle_id'],'investigate','acceptance-reviewer')
            report['checks']['review_not_training_label']=engine.status()['counts']['feedback']==0
            backup(root/'A',root/'backup');restore(root/'backup',root/'restored')
            recovered=PilotEngine(root/'restored')
            report['checks']['backup_restore']=recovered.submit(payload)==saved and recovered.status()['counts']==engine.status()['counts']
            report['checks']['restored_token_not_reused']=not (root/'restored'/'pilot-token.txt').exists()
        if 'pressfit' in manifest['processes']:
            # Synthetic fixtures exist ONLY in this temporary tenant, never in the
            # source state or shipped snapshot. This tests operations, not accuracy.
            install_snapshot(root/'snapshot',root/'learning-fixture')
            report['learning']=learning_acceptance(root/'learning-fixture')
            report['checks']['learning_lifecycle']=all(report['learning'].values())
        report['passed']=bool(first and all(report['checks'].values()))
    write_json(out/'acceptance.json',report)
    rows=''.join('<tr>'+''.join('<td>'+html.escape(str(p.get(k,'—')))+'</td>' for k in
        ('process','runtime_ok','mode','median_ms','max_ms','error'))+'</tr>' for p in report['processes'])
    body=f'''<!doctype html><html lang="ko"><meta charset="utf-8"><title>MILG 설치 검증</title>
<style>body{{font:15px system-ui;max-width:1100px;margin:40px auto;color:#183044}}table{{width:100%;border-collapse:collapse}}td,th{{padding:12px;border-bottom:1px solid #dde4ed;text-align:left}}.notice{{padding:20px;background:#f2f5ff}}pre{{white-space:pre-wrap}}</style>
<h1>MILG · 로컬 설치 검증</h1><p class="notice">종합 결과: {'통과' if report['passed'] else '보완 필요'} · 공개 예시 입력 재생 시험입니다. 공장 품질 정확도·고객 ROI·장기 가동 성능을 증명하지 않습니다. 시간은 이 PC의 직접 엔진 호출 측정입니다.</p>
<table><thead><tr><th>등록 모델</th><th>호출 성공</th><th>분석 상태</th><th>중앙값 ms</th><th>최대 ms</th><th>오류</th></tr></thead><tbody>{rows}</tbody></table><h2>운영 확인</h2><pre>{html.escape(json.dumps(report['checks'],ensure_ascii=False,indent=2))}</pre></html>'''
    (out/'acceptance.html').write_text(body,encoding='utf-8')
    return report


def learning_acceptance(state):
    from datetime import timedelta
    from .learning import LearningManager
    engine=PilotEngine(state);manager=LearningManager(engine)
    context={'machine_id':'ISOLATED-FIXTURE','part_number':'SYNTHETIC','program':'verification-v1'}
    manager.configure('pressfit',context)
    for i in range(100):
        bad=i%20>=10
        request=dict(process='pressfit',cycle_id='fixture-'+str(i),lot_id='L'+str(i//20),context=context,
            demo=False,observed_at=(datetime(2020,1,1,tzinfo=timezone.utc)+timedelta(seconds=i)).isoformat(),
            rows=[dict(units={'force':'N','displacement':'mm'},displacement=list(range(8)),
                       force=[v*(100 if bad else 10)+(i%5)*v*.01 for v in range(8)])])
        engine.submit(request)
        engine.feedback(request['cycle_id'],'fail' if bad else 'pass','fixture','SYNTHETIC-TEST',source_id=request['cycle_id'])
    for i in range(5):manager.close_lot('pressfit',context,'L'+str(i),'fixture')
    result=manager.tick()
    learned=result['policies'][0]['status']=='activated'
    prediction=engine.predict('pressfit',request['rows'],context)
    restarted=PilotEngine(state).predict('pressfit',request['rows'],context)
    manager.tick()
    single=len(manager.status()['runs'])==1
    engine.feedback('fixture-0','unknown','fixture','synthetic label revision')
    revoked=manager.tick()['policies'][0]['status']=='rolled_back'
    fallback=engine.predict('pressfit',request['rows'],context)['mode']=='needs_normal_reference'
    return dict(automatic_training_and_activation=learned,
                registered_model_inference=bool(prediction.get('field_model_id')),
                restart_keeps_model=restarted.get('field_model_id')==prediction.get('field_model_id'),
                no_duplicate_learning=single,label_revision_rollback=revoked,fallback_without_quality_claim=fallback)


def build_package(state, out, with_wheels=False):
    """Pin current installed dependencies; do not claim bundled wheels/offline setup."""
    out=Path(out).resolve();out.mkdir(parents=True,exist_ok=False)
    repo=Path(__file__).resolve().parents[2]
    for package in ('mlng_ai','milg_engine'):
        shutil.copytree(repo/'ai'/package,out/'ai'/package,ignore=shutil.ignore_patterns('__pycache__','outputs','*.pyc','experiments'))
    shutil.copytree(repo/'web',out/'web',ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    snapshot(state,out/'snapshot')
    dependencies=('numpy','pandas','scipy','scikit-learn','joblib','pyyaml','openpyxl','h5py','threadpoolctl','pyarrow',
                  'cloudpickle','narwhals','python-dateutil','tzdata','et-xmlfile','six')
    versions={name:importlib.metadata.version(name) for name in dependencies}
    (out/'requirements-runtime.txt').write_text('\n'.join(f'{k}=={v}' for k,v in versions.items())+'\n',encoding='utf-8')
    for name in ('Start-MilgDelivery.ps1','Install-MilgStartup.ps1'):shutil.copyfile(repo/'tools'/name,out/name)
    docs=repo/'ai'/'PRECONTRACT_READINESS_KO.md'
    shutil.copyfile(docs,out/'READ_ME_KO.md')
    shutil.copyfile(repo/'ai'/'AUTOMATIC_LEARNING_KO.md',out/'AUTOMATIC_LEARNING_KO.md')
    write_json(out/'learning-policy.example.json',{'process':'pressfit','context':{
        'machine_id':'REPLACE_MACHINE','part_number':'REPLACE_PART','program':'REPLACE_PROGRAM_VERSION'},
        'settings':{'enabled':True,'mode':'quality'},'actor':'MILG-deployment-engineer'})
    if with_wheels:
        subprocess.run([sys.executable,'-m','pip','download','--only-binary=:all:',
                        '-r',str(out/'requirements-runtime.txt'),'-d',str(out/'wheels')],check=True)
    result=acceptance(state,out/'verification')
    if not result['passed']:raise ValueError('Acceptance failed; package is NOT approved: '+str(out))
    manifest={'format':'milg_delivery_v1','python':platform.python_version(),'dependencies':versions,
        'platform':platform.platform(),'contains_tenant_data':False,'offline_dependencies_bundled':with_wheels,
        'files':{p.relative_to(out).as_posix():digest(p) for p in out.rglob('*') if p.is_file()}}
    write_json(out/'delivery-manifest.json',manifest)
    archive=Path(shutil.make_archive(str(out),'zip',out))
    archive.with_suffix('.zip.sha256').write_text(digest(archive)+'  '+archive.name+'\n',encoding='utf-8')
    return archive


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('command',choices=['check','snapshot','install','backup','restore','package','verify-package'])
    p.add_argument('--state',type=Path);p.add_argument('--source',type=Path);p.add_argument('--out',type=Path)
    p.add_argument('--with-wheels',action='store_true',help='Download wheels for the current Python/OS/architecture')
    a=p.parse_args()
    if a.command=='verify-package':
        spec=read(a.source/'delivery-manifest.json')
        for name,checksum in spec['files'].items():
            if digest(inside(a.source,name))!=checksum:raise ValueError('Package checksum mismatch: '+name)
        if platform.python_version().split('.')[:2]!=spec['python'].split('.')[:2]:
            raise ValueError('Use the recorded Python major/minor version: '+spec['python'])
        for name,version in spec['dependencies'].items():
            if importlib.metadata.version(name)!=version:raise ValueError('Runtime version differs: '+name+'=='+version)
        print('Package integrity verified. Load only from a trusted source.');return
    if a.command=='check':
        result=acceptance(a.state,a.out);print(json.dumps(result,ensure_ascii=False,indent=2))
        if not result['passed']:raise SystemExit(1)
    elif a.command=='snapshot': print(snapshot(a.state,a.out))
    elif a.command=='install': print(install_snapshot(a.source,a.state))
    elif a.command=='backup': print(backup(a.state,a.out))
    elif a.command=='restore': print(restore(a.source,a.state))
    else: print(build_package(a.state,a.out,a.with_wheels))


if __name__=='__main__':main()
