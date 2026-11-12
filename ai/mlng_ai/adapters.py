"""Versioned raw-input contracts shared by training and engine inference."""
import json
import shutil
import uuid
from pathlib import Path
import numpy as np
import pandas as pd

RECIPES={'screw_stage_torque_angle_v1','screw_stage_torque_angle_v2','aursad_post_operation_progress_shapes_v1',
         'clamping_torque_v1','mct_sensor_columns_v1','causalman_scalar_v1',
         'injection_kamp_summary_v1','heat_kamp_summary_v1','scatim_waveform_summary_v1','spot_weld_aisi1010_v1','press_pump_window_v1','heat_tempering_hrc_v1','press_p2s_force_v1','spot_weld_rsw_summary_v1','ddacs_parameters_v1','extra_numeric_v1','widget_numeric_v1','scenario_numeric_v1'}

REGRESSION_TARGETS = {'preload_kN','batch_defect_rate','scatim_part_weight','spot_weld_nugget_diameter_mm','press_pump_anomaly_score','final_hardness_hrc','ddacs_sim_min_thickness_mm','gmaw_penetration_mm','gmaw_height_mm','gmaw_width_mm','tube_collapse_mm','scenario_estimate'}

def recipe_of(artifact):
    recipe=artifact.get('feature_recipe')
    if not recipe and artifact.get('dataset')=='clamping_48_trials':recipe='clamping_torque_v1'
    if recipe not in RECIPES:raise ValueError('Unsupported input recipe')
    return recipe

def supports(recipe,df,columns=()):
    cols=set(df.columns)
    if recipe in ('screw_stage_torque_angle_v1','screw_stage_torque_angle_v2'):return 'tightening steps' in cols or set(columns)<=cols
    if recipe=='aursad_post_operation_progress_shapes_v1':
        return {'output_double_register_25',*[f'actual_TCP_force_{i}' for i in range(6)]}<=cols or set(columns)<=cols
    if recipe=='clamping_torque_v1':return {'torque_values','angle_values'}<=cols or set(columns)<=cols
    if recipe=='causalman_scalar_v1':return 'PF_M1_T1_Force' in cols
    return bool(columns) and set(columns)<=cols

def input_features(recipe,df,columns):
    if recipe not in RECIPES:raise ValueError('Unknown feature recipe')
    if recipe in ('screw_stage_torque_angle_v1','screw_stage_torque_angle_v2') and 'tightening steps' in df:
        from .train_screw import curve_features
        if recipe.endswith('_v2'):
            from .shared_private import screw_features_v2
            curve_features=screw_features_v2
        work=pd.DataFrame([curve_features(r) for r in df.to_dict('records')])
    elif recipe=='aursad_post_operation_progress_shapes_v1' and 'output_double_register_25' in df:
        from .extra_screw import signal_features
        channels=['output_double_register_25']+[f'actual_TCP_force_{i}' for i in range(6)]
        rows=[]
        for row in df.to_dict('records'):
            arrays=[np.asarray(row[c],float) for c in channels]
            if any(a.ndim!=1 or len(a)<3 or not np.isfinite(a).all() for a in arrays) or len({len(a) for a in arrays})!=1:
                raise ValueError('AURSAD requires seven equal-length finite measured sequences')
            x={}
            for c,a in zip(channels,arrays):x.update(signal_features(a,c))
            rows.append(x)
        work=pd.DataFrame(rows)
    elif recipe=='clamping_torque_v1' and 'torque_values' in df:
        from .extra_screw import describe_curve,signal_features
        rows=[]
        for r in df.to_dict('records'):
            info=describe_curve(r['angle_values'],r['torque_values'])
            x=signal_features(r['torque_values'],'torque');x['angle_span']=info['angle_span'];rows.append(x)
        work=pd.DataFrame(rows)
    elif recipe=='scatim_waveform_summary_v1' and {'pressure','pressure_time','flow','flow_time'}<=set(df.columns):
        from .pack_waveforms import waveform
        work = pd.DataFrame([{channel+'_'+key:value
                              for channel in ('pressure','flow')
                              for key,value in waveform(row[channel],row[channel+'_time']).items()}
                             for row in df.to_dict('records')])
    elif recipe=='press_p2s_force_v1':
        from .press_quality import force_features
        work = pd.DataFrame([force_features(row) for row in df.to_dict('records')])
    elif recipe=='ddacs_parameters_v1':
        from .ddacs import parameter_features
        work = pd.DataFrame([parameter_features(row) for row in df.to_dict('records')])
    elif recipe=='press_pump_window_v1':
        from .press_pump import window_features
        work = pd.DataFrame([window_features(row) for row in df.to_dict('records')])
    elif recipe=='causalman_scalar_v1':
        raw=[c for c in columns if not c.endswith('_relative_to_tolerance')]
        work=df[raw].astype(float).copy()
        for c in columns:
            if c.endswith('_relative_to_tolerance'):
                base=c.removesuffix('_relative_to_tolerance');lo=df[base+'_LTL'].to_numpy(float);hi=df[base+'_UTL'].to_numpy(float)
                if not np.isfinite(lo).all() or not np.isfinite(hi).all() or (hi<=lo).any():raise ValueError('Invalid tolerances')
                work[c]=(work[base].to_numpy()-(lo+hi)/2)/(hi-lo)
    else:work=df.copy()
    missing=set(columns)-set(work.columns)
    if missing:raise ValueError('Required measured features missing: '+str(sorted(missing)))
    work=work[columns].astype(float).reset_index(drop=True)
    if np.isinf(work.to_numpy()).any():raise ValueError('Infinite measured features')
    # NaNs from absent screw stages are handled by the saved training-only imputer.
    if recipe not in ('screw_stage_torque_angle_v1','screw_stage_torque_angle_v2','mct_sensor_columns_v1',
                      'injection_kamp_summary_v1','heat_kamp_summary_v1') and work.isna().any().any():raise ValueError('Nonfinite measured features')
    if work.isna().all(axis=1).any():raise ValueError('No valid measured features')
    return work

def standardize(df,prep):
    columns=prep['columns'];a=df[columns].to_numpy(float)
    a=np.where(np.isfinite(a),a,np.asarray(prep['imputation']))
    return np.clip((a-np.asarray(prep['mean']))/np.asarray(prep['std']),*prep.get('clip',[-8,8]))

def _input_evidence(features,scaled,model=None,limit=4):
    """Report measured evidence honestly; nonlinear models get deviation cues, not fake attribution."""
    columns=list(features.columns);coef=None
    if model is not None and hasattr(model,'coef_') and np.asarray(model.coef_).shape==(1,len(columns)):
        coef=np.asarray(model.coef_[0],float)
    rows=[]
    for i in range(len(features)):
        if coef is None:
            scores=np.abs(scaled[i]);order=np.argsort(-scores)[:limit];method='training_standardized_deviation'
        else:
            contribution=coef*scaled[i];order=np.argsort(-np.abs(contribution))[:limit]
            scores=contribution;method='linear_model_contribution'
        rows.append([{'feature':columns[j], 'measured_value':None if not np.isfinite(features.iloc[i,j]) else float(features.iloc[i,j]),
                      'standardized_value':float(scaled[i,j]), 'score':float(scores[j]), 'method':method}
                     for j in order])
    return rows

def register_external(router,process,source,kind,approved_by):
    """Administrative local artifacts only; joblib must be generated by trusted code."""
    from .routing import digest
    source=Path(source)
    if kind=='specialist':
        import joblib
        artifact=joblib.load(source)
        recipe=recipe_of(artifact);prep=artifact['preprocessing'];cols=prep['columns']
        if recipe=='widget_numeric_v1' and artifact.get('evaluation_gate') is not True:
            raise ValueError('Widget candidate did not pass independent grouped evaluation')
        if len(cols)!=artifact['model'].n_features_in_:raise ValueError('Model/preprocessing size mismatch')
        for k in ('imputation','mean','std'):
            if len(prep[k])!=len(cols) or not np.isfinite(prep[k]).all():raise ValueError('Invalid preprocessing')
        if (np.asarray(prep['std'])<=0).any():raise ValueError('Invalid standard deviation')
        if not hasattr(artifact['model'],'predict_proba') and artifact.get('target') not in REGRESSION_TARGETS:raise ValueError('Unsupported regressor')
        if hasattr(artifact['model'],'predict_proba') and not np.isfinite(artifact.get('threshold',np.nan)):
            raise ValueError('Finite validation threshold required')
        contract={'recipe':recipe,'columns':cols};synthetic=bool(artifact.get('synthetic',False));filename='model.joblib'
    else:
        artifact=json.loads(source.read_text(encoding='utf-8'))
        if artifact.get('regression_gate',{}).get('passed') is not True:raise ValueError('Joint regression gate failed')
        if kind=='joint_v2':
            from .shared_private import validate_bundle
            validate_bundle(artifact)
            if process not in artifact['processes']:raise ValueError('Process absent from joint model')
        else:
            from .model import CumulativeModel
            if artifact.get('format')!='milg_joint_v1':raise ValueError('Invalid joint bundle')
            model=CumulativeModel.from_dict(artifact['model'])
            if process not in model.procs:raise ValueError('Process absent from joint model')
        if process not in artifact['adapters']:raise ValueError('Missing input adapter')
        contract=artifact['adapters'][process];synthetic=contract.get('synthetic',False);filename='model.json'
    folder=router.directory/'routed_models'/uuid.uuid4().hex;folder.mkdir(parents=True)
    shutil.copyfile(source,folder/filename)
    entry={'kind':kind,'approved_by':approved_by,'selected':None,'synthetic':synthetic,
           'model':(folder/filename).relative_to(router.directory).as_posix(),'sha256':digest(folder/filename),
           'input_contract':contract['recipe'],'feature_columns':contract['columns']}
    routes=router.routes()
    if router.registry.exists():shutil.copyfile(router.registry,router.directory/'routes.previous.json')
    routes[process]=entry;tmp=router.registry.with_suffix('.tmp')
    tmp.write_text(json.dumps(routes,indent=2),encoding='utf-8');tmp.replace(router.registry)
    return entry

def predict_external(router,process,route,df):
    from .routing import digest
    path=router.directory/route['model']
    if digest(path)!=route['sha256']:raise ValueError('Registered model checksum mismatch')
    if route['kind']=='specialist':
        import joblib
        artifact=joblib.load(path);prep=artifact['preprocessing'];recipe=recipe_of(artifact)
        features=input_features(recipe,df,prep['columns']);X=standardize(features,prep)
        if recipe == 'spot_weld_aisi1010_v1':
            allowed = {tuple(pair) for pair in artifact['supported_settings']}
            if any(tuple(row) not in allowed for row in features.to_numpy()):
                raise ValueError('Unsupported AISI 1010 demo settings: use a recorded current/time pair')
        if recipe == 'heat_tempering_hrc_v1':
            for column, (low, high) in artifact['bounds'].items():
                if not features[column].between(low, high).all():
                    raise ValueError('Outside published tempering input range: '+column)
        if recipe == 'ddacs_parameters_v1':
            for column, (low, high) in artifact['bounds'].items():
                if not features[column].between(low-1e-9, high+1e-9).all():
                    raise ValueError('Outside supported DDACS parameter range: '+column)
            for row in df.to_dict('records'):
                if not any(row.get('geometry') == geometry['geometry'] and
                           all(np.isclose(row[key], geometry[key], rtol=0, atol=1e-9)
                               for key in ('curvature_radius','bottom_radius','wall_angle'))
                           for geometry in artifact['geometries']):
                    raise ValueError('Unsupported DDACS geometry configuration')
        model=artifact['model'];is_regression=artifact.get('target') in REGRESSION_TARGETS
        if recipe == 'press_pump_window_v1':
            scores = -model.score_samples(X)
        else:
            scores=model.predict(X) if is_regression else model.predict_proba(X)[:,1]
        evidence=_input_evidence(features,X,model)
        rows=[{'i':i,artifact['target']:float(np.clip(v,0,1) if artifact['target']=='batch_defect_rate' else v),
               'top':evidence[i] if artifact['target']!='preload_kN' else []} if is_regression else
              {'i':i,'p':float(v),'risk':float(v),'alert':bool(v>artifact['threshold']),
               'top':evidence[i],'evidence_note':'선형 기여도' if evidence[i] and evidence[i][0]['method']=='linear_model_contribution' else '학습 기준 대비 측정 편차; 모델 원인 기여도는 아님'}
              for i,v in enumerate(scores)]
        target=artifact['target']
    elif route['kind']=='joint_v2':
        from .shared_private import predict_frame
        artifact=json.loads(path.read_text(encoding='utf-8'))
        adapter=artifact['adapters'][process]
        features=input_features(adapter['recipe'],df,adapter['columns'])
        scaled=standardize(features,adapter['preprocessing'])
        scores=predict_frame(artifact,process,df);evidence=_input_evidence(features,scaled)
        policy=artifact['processes'][process];threshold=policy['threshold']
        rows=[{'i':i,'p':float(v),'risk':float(v),'alert':bool(v>threshold),'top':evidence[i],
               'evidence_note':'학습 기준 대비 측정 편차; 신경망 원인 기여도로 해석하지 않음'} for i,v in enumerate(scores)]
        target=adapter['target']
    else:
        from .joint_engine import semantic_transform
        from .model import CumulativeModel
        artifact=json.loads(path.read_text(encoding='utf-8'));adapter=artifact['adapters'][process]
        features=input_features(adapter['recipe'],df,adapter['columns']);X=semantic_transform(features,adapter)
        model=CumulativeModel.from_dict(artifact['model']);mu=model.predict(process,X,np.ones(len(X)))[0]
        scores=model.prob(process,mu);threshold=artifact['thresholds'][process]
        rows=[{'i':i,'p':float(v),'risk':float(v),'alert':bool(v>threshold),'top':model.explain(process,X[i])[:3]} for i,v in enumerate(scores)]
        target=adapter['target']
    return {'process':process,'mode':'registered_model','adapter':route['kind'],'target':target,
            'synthetic':route['synthetic'],'input_contract':route['input_contract'],
            'shared_training_includes_synthetic':route['kind']=='joint' and any(a.get('synthetic') for a in artifact['adapters'].values()),
            'shared_weight':artifact['processes'][process]['shared_weight'] if route['kind']=='joint_v2' else None,
            'model_sha256':route['sha256'],'phase':'post_operation','rows':rows}
