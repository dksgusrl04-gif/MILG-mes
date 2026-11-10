"""Conservative process recognition and read-only recipe preview.
Signature support is engineering evidence, never a calibrated probability.
"""
from .profiles import ProfileRuntime, load_profile

TEMPLATES = {'mct':'mct_endmill', 'screw':'screw_tightening'}
ALIASES = {'mct':'mct','mach':'mct','milling':'mct','screw':'screw',
           'screw_tightening':'screw','pressfit':'pressfit','causalman_pressfit':'pressfit'}

def family(process):
    if process.startswith('mct_'): return 'mct'
    if process.startswith(('screw_','aursad_','clamping_')): return 'screw'
    return ALIASES.get(process)

def recognize(df, declared=''):
    cols = set(df.columns)
    evidence = {}
    # Require process-specific combinations; force or torque alone is ambiguous.
    if 'tightening steps' in cols:
        has_torque_angle = False
        for steps in df['tightening steps']:
            if isinstance(steps,list) and any(isinstance(s,dict) and
                {'torque values','angle values'} <= set((s.get('graph') or {})) for s in steps):
                has_torque_angle = True
        if has_torque_angle: evidence['screw'] = ['stage_graph:torque_and_angle']
    if {'cut_load','time_seconds','rpm','feed','depth'} <= cols:
        evidence['mct'] = ['cut_load_time_and_cutting_conditions']
    if {'F_z_rms','M_z_rms','A_z_rms','tool_id'} <= cols:
        evidence.setdefault('mct',[]).append('milling_sensor_summary')
    if {'smcAC','smcDC','vib_table','vib_spindle'}<=cols:
        evidence.setdefault('mct',[]).append('nasa_milling_sensor_summary')
    if {'force_x_rms','force_y_rms','force_z_rms','torque_spindle_rms'}<=cols:
        evidence.setdefault('mct',[]).append('stiehl_milling_sensor_summary')
    if {'PF_M1_T1_Force','PF_M1_T1_Fmax','PF_M1_T2_Force','PF_M1_T2_Fmax'} <= cols:
        evidence['pressfit'] = ['causalman_pressfit_schema']
    if {'output_double_register_25',*[f'actual_TCP_force_{i}' for i in range(6)]}<=cols:
        evidence['screw']=['aursad_measured_sequence_contract']
    if {'torque_values','angle_values'}<=cols:
        evidence['screw']=['clamping_torque_angle_contract']
    declared_family = family(declared)
    candidates = sorted(evidence)
    if len(candidates)>1:
        status, selected = 'needs_confirmation', None
    elif candidates and declared_family and candidates[0] != declared_family:
        status, selected = 'conflict', None
    elif candidates:
        status, selected = 'recognized', candidates[0]
    elif declared_family:
        status, selected = 'declared_only', declared_family
    else:
        status, selected = 'unknown', None
    return {'status':status,'family':selected,'declared_process':declared,
            'candidates':[{'family':k,'evidence':v} for k,v in sorted(evidence.items())],
            'evidence_type':'schema_rules_not_probability',
            'suggested_template':TEMPLATES.get(selected)}

def analyze(df, declared='', template_id=None):
    result = recognize(df,declared)
    if template_id:
        template = load_profile(template_id)
        if result['family'] and template['kind'] != result['family']:
            result['status'] = 'conflict'
        elif result['family'] is None:
            # Naming a template is not evidence that contradictory input is valid.
            result['requested_template'] = template_id
    else:
        template = load_profile(result['suggested_template']) if result['suggested_template'] else None
    if result['status'] in ('conflict','needs_confirmation','unknown'):
        return {'recognition':result,'mode':'needs_confirmation','template':None,'rows':[]}
    if template is None:
        return {'recognition':result,'mode':'no_curve_template','template':None,'rows':[]}
    rt = ProfileRuntime(template,'read-only-preview')
    output=[]
    for i,row in enumerate(df.to_dict('records')):
        try:
            r=rt.observe(row)
            output.append({'i':i,**r})
        except (ValueError,TypeError,KeyError) as e:
            output.append({'i':i,'available':False,'reason':str(e)})
    return {'recognition':result,'mode':'template_analysis_only',
            'template':{'id':template['id'],'version':template['version'],
                        'sha256':rt.fingerprint},
            'rows':output,'state_persisted':False,'quality_prediction':None}

def choose_registered_process(recognition, routes, df=None):
    """Only select a uniquely eligible real-process route.
    Never infer use of a synthetic model from a press-fit-like schema.
    """
    f=recognition['family']
    if recognition['status'] not in ('recognized','declared_only') or not f:
        return None
    eligible=[key for key,r in routes.items() if family(key)==f and not r.get('synthetic')]
    if df is not None:
        from .adapters import RECIPES,supports
        eligible=[key for key in eligible if routes[key]['input_contract'] not in RECIPES or
                  supports(routes[key]['input_contract'],df,routes[key].get('feature_columns',()))]
    return eligible[0] if len(eligible)==1 else None

if __name__ == '__main__':
    import argparse
    import json
    from pathlib import Path
    import pandas as pd
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('file'); p.add_argument('--process',default='')
    p.add_argument('--template'); p.add_argument('--out',required=True)
    a=p.parse_args(); path=Path(a.file)
    df=pd.DataFrame(json.loads(path.read_text(encoding='utf-8'))) if path.suffix.lower()=='.json' else pd.read_csv(path)
    result=analyze(df,a.process,a.template)
    out=Path(a.out);out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(result['recognition']['status'],result['recognition']['family'],result['mode'],out)
