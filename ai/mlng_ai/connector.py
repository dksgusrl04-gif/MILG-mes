"""Explicit completed-cycle CSV mapping, whole-file validation and resumable local submission.

Never guesses process, sensor units or raw-waveform summaries. CLI validate is
read-only for event history; send is explicit, idempotent and restartable.
"""
import argparse
import csv
import json
import math
from datetime import datetime
from pathlib import Path

from .delivery import read, inspect_release
from .pilot import PilotEngine, context_key, write_json


def make_template(state, process, out):
    release,manifest,routes,_=inspect_release(state)
    if process not in routes:raise ValueError('CSV template needs an explicitly registered numeric process')
    sample=read(release/'examples'/f'{process}.json')
    row=sample['rows'][0]
    if any(not isinstance(v,(int,float,str)) or isinstance(v,bool) for v in row.values()):
        raise ValueError('This process requires nested/waveform JSON; use /api/cycle, not flat CSV')
    units=manifest['processes'][process].get('input_units',{})
    mapping={'format':'milg_csv_mapping_v1','process':process,'demo':True,
        'columns':{'cycle_id':'cycle_id','lot_id':'lot_id','observed_at':'observed_at'},
        'context':sample['context'], 'template_version':manifest['id'],
        'features':{k:{'column':k,'type':'number' if isinstance(v,(float,int)) else 'string',
                       'source_unit':units.get(k,'UNCONFIRMED') or '1', 'engine_unit':units.get(k,'UNCONFIRMED') or '1',
                       'scale':1.0,'offset':0.0} for k,v in row.items()}}
    out=Path(out);out.mkdir(parents=True,exist_ok=True)
    write_json(out/'mapping.json',mapping)
    data={'cycle_id':'CSV-DEMO-001','lot_id':'EXAMPLE-LOT','observed_at':'2026-11-12T00:00:00+00:00',**row}
    with (out/'cycles.csv').open('w',encoding='utf-8-sig',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=list(data));writer.writeheader();writer.writerow(data)
    return out


def prepare(state, csv_path, mapping_path):
    mapping=read(mapping_path)
    if mapping.get('format')!='milg_csv_mapping_v1':raise ValueError('Unsupported mapping version')
    if not isinstance(mapping.get('demo'),bool):raise ValueError('Explicit demo=true/false required')
    release,manifest,routes,_=inspect_release(state)
    process=mapping['process']
    if process not in routes:raise ValueError('Explicit registered numeric process required')
    if mapping.get('template_version')!=manifest['id']:
        raise ValueError('Release changed; recheck and regenerate mapping before sending')
    context_key(mapping['context'])
    required=set(routes[process].get('feature_columns',[]))
    if required-set(mapping['features']):raise ValueError('Required feature mappings missing')
    units=manifest['processes'][process].get('input_units',{})
    metadata=mapping['columns']
    if not {'cycle_id','lot_id','observed_at'}<=set(metadata):raise ValueError('Cycle, LOT and observation timestamp mappings required')
    for name,spec in mapping['features'].items():
        if spec['type'] not in ('number','string'):raise ValueError('Unsupported mapped type')
        if spec['type']=='number':
            if not all(isinstance(spec.get(k),str) and spec[k].strip() and (mapping['demo'] or spec[k]!='UNCONFIRMED') for k in ('source_unit','engine_unit')):
                raise ValueError('Confirm source and engine units before sending: '+name)
            if name in units and spec['engine_unit']!=(units[name] or '1'):raise ValueError('Engine unit differs from release: '+name)
            scale=float(spec.get('scale',1));offset=float(spec.get('offset',0))
            if not math.isfinite(scale) or scale==0 or not math.isfinite(offset):raise ValueError('Invalid conversion')
            if spec['source_unit']!=spec['engine_unit'] and scale==1 and offset==0:
                raise ValueError('Unit change needs explicit conversion')
    payloads=[];ids=set();last_time=None
    with Path(csv_path).open(encoding='utf-8-sig',newline='') as stream:
        reader=csv.DictReader(stream)
        if not reader.fieldnames or len(reader.fieldnames)!=len(set(reader.fieldnames)):raise ValueError('Unique CSV headers required')
        expected={*metadata.values(),*(s['column'] for s in mapping['features'].values())}
        if expected-set(reader.fieldnames):raise ValueError('CSV columns missing: '+str(sorted(expected-set(reader.fieldnames))))
        for number,source in enumerate(reader,2):
            try:
                if None in source or any(v is None for v in source.values()):raise ValueError('Malformed CSV row')
                cycle=source[metadata['cycle_id']].strip();lot=source[metadata['lot_id']].strip()
                if not cycle or len(cycle)>160 or not lot or cycle in ids:raise ValueError('Empty or duplicate cycle/LOT')
                observed=source[metadata['observed_at']].strip()
                timestamp=datetime.fromisoformat(observed.replace('Z','+00:00'))
                if timestamp.utcoffset() is None:raise ValueError('Timestamp needs timezone')
                if last_time is not None and timestamp<last_time:raise ValueError('CSV must be chronological')
                last_time=timestamp;ids.add(cycle);features={}
                for target,spec in mapping['features'].items():
                    raw=source[spec['column']].strip()
                    if not raw:raise ValueError('Missing measured input: '+target)
                    if spec['type']=='string':features[target]=raw
                    else:
                        value=float(raw)*float(spec.get('scale',1))+float(spec.get('offset',0))
                        if not math.isfinite(value):raise ValueError('Nonfinite input: '+target)
                        features[target]=value
                payloads.append({'process':process,'cycle_id':cycle,'lot_id':lot,'observed_at':observed,
                    'context':mapping['context'],'demo':mapping['demo'],'rows':[features],
                    'connector':{'format':mapping['format'],'template_version':mapping['template_version']}})
            except (ValueError,TypeError) as exc:raise ValueError(f'CSV row {number}: {exc}') from exc
    if not payloads:raise ValueError('CSV has no cycles')
    return payloads


def run(state, csv_path, mapping_path, out, send=False):
    payloads=prepare(state,csv_path,mapping_path)
    engine=PilotEngine(state)
    # Validate all inputs and existing IDs BEFORE any writes. Runtime interruption
    # during send can still leave a prefix committed; exact retries are idempotent.
    for payload in payloads:
        result=engine.predict(payload['process'],payload['rows'],payload['context'])
        if not result.get('rows') or any(r.get('input_valid') is False for r in result['rows']):
            raise ValueError('Engine rejected mapped cycle: '+payload['cycle_id'])
        import hashlib
        canonical=json.dumps(payload,sort_keys=True,ensure_ascii=False,allow_nan=False)
        with engine.db() as db:
            found=db.execute('SELECT request_hash FROM events WHERE id=?',(payload['cycle_id'],)).fetchone()
        if found and found[0]!=hashlib.sha256(canonical.encode()).hexdigest():raise ValueError('Existing cycle conflicts: '+payload['cycle_id'])
    report={'mode':'send' if send else 'validate_only','cycles':len(payloads),'submitted':0,
            'process':payloads[0]['process'],'demo':payloads[0]['demo'],'quality_validated':False}
    if send:
        for payload in payloads:
            engine.submit(payload);report['submitted']+=1
    write_json(Path(out),report)
    return report


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('command',choices=['template','validate','send']);p.add_argument('--state',required=True,type=Path)
    p.add_argument('--process');p.add_argument('--csv',type=Path);p.add_argument('--mapping',type=Path);p.add_argument('--out',required=True,type=Path)
    a=p.parse_args()
    if a.command=='template':print(make_template(a.state,a.process,a.out))
    else:print(json.dumps(run(a.state,a.csv,a.mapping,a.out,a.command=='send'),ensure_ascii=False,indent=2))


if __name__=='__main__':main()
