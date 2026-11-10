"""Build and verify an immutable pilot release before activating its pointer."""
import argparse
import hashlib
import json
import shutil
import uuid
import zipfile
import platform
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from threadpoolctl import threadpool_limits

from .evaluate_processes import metrics
from .joint_engine import grouped_split
from .pilot import PilotEngine, now, write_json
from .routing import Router, digest
from .strict_evaluation import TrainTransform, threshold_on_validation

EXPECTED = ['d125657a4b6d7ea0e4a985a4727ebb78','0bc948a6e8c6e83f72dbe36973131558',
            'a21fea4da2bfe0138d582584ddfe27f5','d9fd687febf93ecba7f9bbea0069e676',
            'eab215fe08d38c30ca0366a0665c4bbd','ddb668b1e4db64aeb910d07f3ee4e010']


def screw_source(public, legacy, stage):
    stage.mkdir(parents=True)
    for i, expected in enumerate(EXPECTED, 1):
        root = public if i <= 4 else legacy
        matches = list(root.glob(f's{i:02}_*.zip'))
        matches = [p for p in matches if hashlib.md5(p.read_bytes()).hexdigest() == expected]
        if len(matches) != 1:
            raise ValueError(f'Exactly one verified s{i:02} archive required in {root}')
        shutil.copyfile(matches[0], stage/matches[0].name)


def screw_candidate(public, sources):
    expected = {digest(p) for p in sources.glob('*.zip')}
    candidates = sorted((public/'PyScrew_candidate_reports').glob('*/report.json'), reverse=True)
    for report in candidates:
        manifest = report.parent/'manifest.json'
        if not manifest.exists():
            continue
        hashes = {r['sha256'] for r in json.loads(manifest.read_text(encoding='utf-8'))['files']}
        if hashes == expected:
            return report.parent
    raise ValueError('No completed screw candidate matches the corrected six archives; run train_screw first')


def train_mct(folder, output):
    from .mct import hmotp_table, nasa_table, stiehl_table, CH, ST
    from .stiehl import LABEL
    tables = {'mct_hmotp': hmotp_table(folder/'hmotp_cuts.csv'), 'mct_nasa': nasa_table(),
              'mct_stiehl': stiehl_table(folder/'stiehl_runs.csv')}
    reports, examples = {}, {}
    for process, (table, raw) in tables.items():
        print('Training and evaluating', process, flush=True)
        if process == 'mct_nasa':
            columns = [c for c in ('smcAC','smcDC','vib_table','vib_spindle','AE_table','AE_spindle') if c in table.df]
        else:
            # Portable Stiehl recipe uses physical force and spindle torque only.
            # Axis motor torque exists only on M1 and is not an interchangeable channel.
            channels = CH if process == 'mct_hmotp' else {'force_x','force_y','force_z','torque_spindle'}
            columns = [c for c in table.df if c.rpartition('_')[0] in channels and c.rpartition('_')[2] in ST]
        X = table.df[columns]
        y = table.df.tool_ng.to_numpy(int)
        groups = raw.machine_id if process == 'mct_stiehl' else table.df.tool_id
        splits = grouped_split(groups)
        masks = [splits == s for s in ('train','validation','test')]
        if any(set(y[m]) != {0,1} for m in masks):
            raise ValueError(process+': all partitions need both wear-label classes')
        tr, val, test = masks
        transform = TrainTransform().fit(X.loc[tr])
        a,b,c = [transform.transform(X.loc[m]) for m in masks]
        candidates = {'linear': LogisticRegression(max_iter=2000, random_state=42),
                      'nonlinear': HistGradientBoostingClassifier(max_iter=120, max_leaf_nodes=7,
                          min_samples_leaf=10, l2_regularization=2, early_stopping=False, random_state=42)}
        evaluations = {}
        for name, model in candidates.items():
            model.fit(a, y[tr])
            vs = model.predict_proba(b)[:,1]
            threshold = threshold_on_validation(y[val], vs, .05)
            evaluations[name] = {'validation': metrics(y[val], vs, threshold),
                                 'test': metrics(y[test], model.predict_proba(c)[:,1], threshold)}
        selected = max(evaluations, key=lambda k: (evaluations[k]['validation']['recall'], evaluations[k]['validation']['auc']))
        chosen = evaluations[selected]
        artifact = {'model': candidates[selected], 'preprocessing': transform.export(),
                    'threshold': chosen['validation']['threshold'], 'process': process,
                    'target': 'public_dataset_wear_threshold', 'feature_recipe': 'mct_sensor_columns_v1'}
        joblib.dump(artifact, output/(process+'.joblib'))
        reports[process] = {'selection': selected, 'metrics': evaluations,
                            'partition_unit': 'machine' if process == 'mct_stiehl' else 'tool',
                            'groups': {s: sorted(set(np.asarray(groups).astype(str)[splits==s])) for s in ('train','validation','test')},
                            'rows': {s: int((splits==s).sum()) for s in ('train','validation','test')},
                            'features': columns, 'source': table.source,
                            'units': {c: ('' if c.endswith('_kurt') else (CH if process=='mct_hmotp' else LABEL).get(c.rpartition('_')[0], ('','source_sensor_unit'))[1]) for c in columns},
                            'label_rule': 'dataset evaluation wear threshold; not universal tool replacement policy'}
        examples[process] = X.loc[test].iloc[:1].to_dict('records')
    return reports, examples


def build(args):
    state, public, legacy = Path(args.state).resolve(), Path(args.public), Path(args.screw_dir)
    identity = 'pilot-'+uuid.uuid4().hex
    release = state/'pilot-releases'/identity
    release.mkdir(parents=True)
    sources = release/'verified-screw-input'
    screw_source(public, legacy, sources)
    candidate = screw_candidate(public, sources)
    report = json.loads((candidate/'report.json').read_text(encoding='utf-8'))
    manifest = {'id': identity, 'created': now(), 'stage': 'precontract_pilot', 'field_validated': False,
                'runtime': {'python': platform.python_version(), 'numpy': np.__version__,
                            'scikit_learn': __import__('sklearn').__version__},
                'processes': {}, 'mct_evaluation': {}, 'screw_source': str(candidate),
                'excluded': ['MATWI pending licensing decision', 'Figshare raw data needs dedicated adapter',
                             'synthetic pressfit is not industrial quality training'],
                'source_hashes': {p.name: digest(p) for p in sources.glob('*.zip')}}
    router = Router(release)
    models = release/'candidates'
    models.mkdir()
    with threadpool_limits(limits=args.threads):
        mct_report, examples = train_mct(Path(args.mct_summary), models)
    manifest['mct_evaluation'] = mct_report
    for process in mct_report:
        router.register(process, models/(process+'.joblib'), 'specialist', 'pilot-installer')
        r = mct_report[process]
        manifest['processes'][process] = {'mode': 'public_benchmark_shadow', 'target': 'dataset_wear_threshold',
            'validation': r['metrics'][r['selection']]['validation'], 'test': r['metrics'][r['selection']]['test'],
            'alert_enabled': r['metrics'][r['selection']]['test']['recall'] >= .1,
            'input_units': r['units'],
            'input': 'explicit sensor summary contract; field channel mapping required', 'quality_release_authority': False}
    archive = sorted(sources.glob('*.zip'))[0]
    with zipfile.ZipFile(archive) as z:
        raw = json.loads(z.read(next(n for n in z.namelist() if n.endswith('.json'))))
    for target, process in [('faulty','screw_tightening'), ('nok','screw_equipment_nok')]:
        selected = report[target]['selected_on_validation']
        if selected not in ('linear','nonlinear'):
            raise ValueError('No validation-selected screw model for '+target)
        source = candidate/(target+'_'+selected+'.joblib')
        artifact = joblib.load(source)
        if artifact['target'] != target or artifact['process'] != 'screw_tightening':
            raise ValueError('Screw artifact target mismatch')
        router.register(process, source, 'specialist', 'pilot-installer')
        manifest['processes'][process] = {'mode': 'public_benchmark_shadow', 'target': target,
            'auto_route': target == 'faulty', 'quality_release_authority': False,
            'alert_enabled': report[target]['candidates'][selected]['test']['recall'] >= .1,
            'validation': report[target]['candidates'][selected]['validation'],
            'test': report[target]['candidates'][selected]['test'],
            'limits': report['limits']}
        examples[process] = [raw]
    manifest['processes']['pressfit'] = {'mode': 'tenant_normal_reference',
        'target': 'force_displacement_deviation', 'quality_release_authority': False,
        'requires': '40 independently confirmed normal cycles, 3 lots, same machine/part/program',
        'limits': 'No learned industrial press-fit defect classifier; sensitivity requires independent failed parts'}
    examples['pressfit'] = [{'force': [0,10,20,28,35,45,60,75], 'displacement': [0,1,2,3,4,5,6,7],
                            'units': {'force':'N','displacement':'mm'}}]
    manifest['routes_sha256'] = digest(release/'routes.json')
    write_json(release/'release.json', manifest)
    pointer = {'release': str(release), 'sha256': digest(release/'release.json')}
    # Full inference and feedback smoke checks use isolated state, before activation.
    check = release/'verification-state'
    write_json(check/'pilot-active.json', pointer)
    engine = PilotEngine(check)
    checks = {}
    example_dir = release/'examples'
    for process, rows in examples.items():
        payload = {'process': process, 'cycle_id': 'example-'+process, 'lot_id': 'EXAMPLE-LOT', 'demo': True,
                   'context': {'machine_id': 'EXAMPLE-MACHINE','part_number':'EXAMPLE-PART','program':'EXAMPLE-v1'}, 'rows': rows}
        write_json(example_dir/(process+'.json'), payload)
        result = engine.submit(payload)
        if len(result['rows']) != 1:
            raise ValueError('Inference verification failed for '+process)
        if engine.submit(payload) != result:
            raise ValueError('Idempotency verification failed')
        engine.feedback(payload['cycle_id'], 'unknown', 'installation-verifier', '', 'software check only')
        checks[process] = {'mode': result['mode'], 'rows': len(result['rows'])}
    lines = ['# MILG 현장 연결용 파일럿 설치 결과', '',
             '공개 자료로 학습한 모델과 현장 입력·추론·검사 결과 저장 경로입니다. 현장 품질 보증은 포함하지 않습니다.', '',
             '| 공정 | 시험 AUC | 검출률 | 정상 오경보 | 실험 알림 |', '|---|---:|---:|---:|---|']
    for process, capability in manifest['processes'].items():
        metric = capability.get('test')
        if metric:
            lines.append(f"| {process} | {metric['auc']:.4f} | {metric['recall']:.2%} | {metric['false_positive_rate']:.2%} | {'사용' if capability['alert_enabled'] else '보류: 검출률 부족'} |")
        else:
            lines.append(f'| {process} | — | — | — | 현장 정상 기준 학습 필요 |')
    lines += ['', 'MCT: 공구 또는 설비 단위 학습/검증/시험 분리, 검증 데이터로 후보·임계값 선택.',
              'Stiehl: 공통 절삭력 3축·주축 토크만 사용. M1에만 존재하는 이송축 토크는 제외.',
              '체결: 수정 s01–s04 및 기존 s05–s06의 체크섬이 일치하는 후보만 적용.',
              'NOK는 장비 판정 라벨이며 체결 강도 정답이 아닙니다. 새 공장 검증 수치가 아닙니다.',
              '압입: N/mm 입력 계약, 파형 특징과 현장 정상 기준 학습. 실제 압입 불량 분류 성능은 미검증.',
              '알림 보류 기준: 공개 시험 검출률 10% 미만. 이 기준은 현장 품질 합격 기준이 아닙니다.',
              'Figshare 신규 원시 자료·MATWI는 이 릴리스의 학습에 포함하지 않았습니다.',
              '검증용 example은 demo=true이며 현장 정상 기준 학습에서 제외합니다.',
              '기존 누적학습 명령은 이 릴리스 모델을 변경하지 못하도록 차단합니다.',
              '검증: 전 공정 추론, 작업 중복 방지, 담당자 피드백을 격리된 저장소에서 확인했습니다.']
    (release/'REPORT_KO.md').write_text('\n'.join(lines), encoding='utf-8')
    write_json(release/'verification.json', {'checks': checks, 'passed': True, 'field_performance_test': False})
    # One atomic pointer activates all contracts and models; prior release remains rollbackable.
    from .service import Service
    service = Service(state)
    with service._exclusive():
        if (state/'pilot-active.json').exists():
            write_json(state/'pilot-previous.json', json.loads((state/'pilot-active.json').read_text(encoding='utf-8')))
        write_json(state/'pilot-active.json', pointer)
    print('Pilot installed:', state, flush=True)
    print('Release:', release, flush=True)
    print('Verified processes:', ', '.join(checks), flush=True)
    print('Field calibration and validation remain site-specific. Pressfit quality classifier is not claimed.', flush=True)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--state', required=True)
    p.add_argument('--public', required=True)
    p.add_argument('--screw-dir', required=True)
    p.add_argument('--mct-summary', required=True)
    p.add_argument('--threads', type=int, default=4)
    args = p.parse_args()
    if args.threads < 1: p.error('threads must be positive')
    build(args)


if __name__ == '__main__':
    main()
