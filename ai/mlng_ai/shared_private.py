"""MILG v2 neural shared encoder with frozen process-specific models.
Train/validation select checkpoints and mixing; test evaluation is a separate command.
This is feature/curve-shape neural learning, not a raw-waveform foundation model.
"""
import argparse
import copy
from datetime import datetime
import hashlib
import json
from pathlib import Path
import numpy as np
import pandas as pd
from .adapters import input_features,standardize
from .evaluate_processes import metrics
from .strict_evaluation import threshold_on_validation

FORMAT='milg_neural_shared_private_v2'

def screw_features_v2(raw):
    from .train_screw import curve_features,STAGES
    result=curve_features(raw)
    for i,name in enumerate(STAGES):
        graph=next((s.get('graph',{}) for s in raw.get('tightening steps',[]) if s['name']==name),{})
        torque=np.asarray(graph.get('torque values',[]),float)
        for j in range(24):result[f'stage_{i}_torque_progress_{j}']=np.nan
        for k in ('gradient_mean','gradient_std','gradient_min','gradient_max','duration','torque_rate_rms'):
            result[f'stage_{i}_{k}']=np.nan
        if len(torque)<2:continue
        # Preserve the recorded order; do not silently sort angle reversals.
        shape=np.interp(np.linspace(0,1,24),np.linspace(0,1,len(torque)),torque)
        for j,v in enumerate(shape):result[f'stage_{i}_torque_progress_{j}']=float(v)
        gradient=np.asarray(graph.get('gradient values',[]),float)
        if len(gradient)==len(torque) and np.isfinite(gradient).all():
            for k,v in [('mean',gradient.mean()),('std',gradient.std()),('min',gradient.min()),('max',gradient.max())]:
                result[f'stage_{i}_gradient_{k}']=float(v)
        time=np.asarray(graph.get('time values',[]),float)
        if len(time)==len(torque) and np.isfinite(time).all() and (np.diff(time)>0).all():
            result[f'stage_{i}_duration']=float(time[-1]-time[0])
            result[f'stage_{i}_torque_rate_rms']=float(np.sqrt(np.mean((np.diff(torque)/np.diff(time))**2)))
    return result

def private_inputs(frame,adapter):
    a=frame[adapter['columns']].to_numpy(float)
    # Keep every measured feature's sign and location, plus explicit missingness.
    return np.column_stack([standardize(frame,adapter['preprocessing']),~np.isfinite(a)]).astype(np.float32)

def networks(dimensions,real_keys):
    import torch.nn as nn
    class Joint(nn.Module):
        def __init__(self):
            super().__init__()
            self.locals=nn.ModuleDict({k:nn.Sequential(nn.Linear(d,64),nn.SiLU(),nn.Linear(64,32),nn.SiLU(),nn.Linear(32,1)) for k,d in dimensions.items()})
            self.projectors=nn.ModuleDict({k:nn.Sequential(nn.Linear(dimensions[k],32),nn.SiLU()) for k in real_keys})
            self.encoder=nn.Sequential(nn.Linear(32,32),nn.SiLU(),nn.Linear(32,16),nn.SiLU())
            self.heads=nn.ModuleDict({k:nn.Linear(16,1) for k in real_keys})
            for head in self.heads.values():nn.init.zeros_(head.weight);nn.init.zeros_(head.bias)
        def local(self,key,x):return self.locals[key](x).squeeze(-1)
        def residual(self,key,x):return self.heads[key](self.encoder(self.projectors[key](x))).squeeze(-1)
    return Joint()

def json_weights(model):return {k:v.detach().cpu().tolist() for k,v in model.state_dict().items()}

def restore(bundle):
    import torch
    with torch.random.fork_rng(devices=[]):model=networks(bundle['dimensions'],bundle['real_processes'])
    state=model.state_dict()
    model.load_state_dict({k:torch.as_tensor(v,dtype=state[k].dtype) for k,v in bundle['weights'].items()},strict=True)
    model.eval();return model

def validate_bundle(bundle):
    if bundle.get('format')!=FORMAT:raise ValueError('Unknown neural bundle format')
    if set(bundle['processes'])!=set(bundle['dimensions']) or set(bundle['adapters'])!=set(bundle['dimensions']):
        raise ValueError('Process contract mismatch')
    for key,policy in bundle['processes'].items():
        if bundle['dimensions'][key]!=2*len(bundle['adapters'][key]['columns']):raise ValueError('Input dimension mismatch')
        if not 0<=policy['shared_weight']<=1 or not np.isfinite(policy['threshold']):raise ValueError('Invalid policy')
        if bundle['adapters'][key]['synthetic'] and policy['shared_weight']!=0:raise ValueError('Synthetic shared influence forbidden')
    model=restore(bundle)
    if any(not np.isfinite(v.detach().numpy()).all() for v in model.state_dict().values()):raise ValueError('Nonfinite weights')

def probabilities(model,key,x,alpha):
    import torch
    with torch.inference_mode():
        tensor=torch.as_tensor(x,dtype=torch.float32);local=model.local(key,tensor)
        base=torch.sigmoid(local)
        shared=torch.sigmoid(local+model.residual(key,tensor)) if alpha and key in model.heads else base
        return ((1-alpha)*base+alpha*shared).cpu().numpy().astype(float)

def predict_frame(bundle,key,frame):
    adapter=bundle['adapters'][key];features=input_features(adapter['recipe'],frame,adapter['columns'])
    return probabilities(restore(bundle),key,private_inputs(features,adapter),bundle['processes'][key]['shared_weight'])

def choose_policy(y,local,joint,fpr):
    baseline_threshold=threshold_on_validation(y,local,fpr)
    baseline=metrics(y,local,baseline_threshold);eligible=[]
    for alpha in (0.,.25,.5,.75,1.):
        scores=(1-alpha)*local+alpha*joint;threshold=threshold_on_validation(y,scores,fpr);m=metrics(y,scores,threshold)
        if m['false_positive_rate']<=fpr+1e-12 and m['auc']>=baseline['auc']-.01 and m['recall']>=baseline['recall']-.03:
            eligible.append((m['recall'],m['auc'],-alpha,alpha,threshold,m))
    selected=max(eligible)
    return {'shared_weight':selected[3],'threshold':selected[4],'validation':selected[5],
            'local_threshold':baseline_threshold,'local_validation':baseline}

def train(data,out,epochs=40,patience=6,batch_size=256,threads=4,fpr=.05):
    import torch
    import torch.nn.functional as F
    torch.set_num_threads(threads)
    dimensions={k:2*len(d['adapter']['columns']) for k,d in data.items()}
    real_keys=[k for k,d in data.items() if not d['adapter']['synthetic']]
    with torch.random.fork_rng(devices=[]):
        torch.manual_seed(42);model=networks(dimensions,real_keys)
    tensors={};history={'local':{},'shared':[]};cache={}
    for key,d in data.items():
        arrays=private_inputs(d['features'],d['adapter']);cache[key]=arrays
        tensors[key]={s:(torch.tensor(arrays[d['split']==s]),torch.tensor(d['y'][d['split']==s],dtype=torch.float32))
                      for s in ('train','validation')}
        # Test features/labels are saved for the separate evaluate command, never
        # forwarded through the network or scored during training/selection.
        np.savez_compressed(out/(key+'.npz'),inputs=arrays,target=d['y'],split=d['split'],groups=d['groups'])
    for key in data:
        net=model.locals[key];opt=torch.optim.AdamW(net.parameters(),lr=.001,weight_decay=.001)
        generator=torch.Generator().manual_seed(42+int(hashlib.sha256(key.encode()).hexdigest()[:6],16))
        x,y=tensors[key]['train'];vx,vy=tensors[key]['validation'];best=float('inf');best_state=copy.deepcopy(net.state_dict());stale=0;logs=[]
        for epoch in range(epochs):
            net.train();order=torch.randperm(len(x),generator=generator)
            for ids in order.split(batch_size):
                opt.zero_grad();loss=F.binary_cross_entropy_with_logits(net(x[ids]).squeeze(-1),y[ids]);loss.backward()
                torch.nn.utils.clip_grad_norm_(net.parameters(),5.);opt.step()
            net.eval()
            with torch.inference_mode():value=float(F.binary_cross_entropy_with_logits(net(vx).squeeze(-1),vy))
            if not np.isfinite(value):raise ValueError('Nonfinite local loss: '+key)
            logs.append({'epoch':epoch+1,'validation_bce':value})
            if value<best-1e-5:best=value;best_state=copy.deepcopy(net.state_dict());stale=0
            else:stale+=1
            if stale>=patience:break
        net.load_state_dict(best_state);history['local'][key]=logs
        print('Private neural model',key,'epochs',len(logs),'best validation BCE',round(best,5),flush=True)
    for p in model.locals.parameters():p.requires_grad_(False)
    trainable=list(model.projectors.parameters())+list(model.encoder.parameters())+list(model.heads.parameters())
    opt=torch.optim.AdamW(trainable,lr=.0005,weight_decay=.001)
    gen=torch.Generator().manual_seed(12042)
    # Initial zero residual is a valid checkpoint: sharing must earn its place.
    def validation_loss():
        model.eval();values=[]
        with torch.inference_mode():
            for key in real_keys:
                x,y=tensors[key]['validation'];values.append(float(F.binary_cross_entropy_with_logits(model.local(key,x)+model.residual(key,x),y)))
        return float(np.mean(values))
    initial_encoder={k:v.detach().clone() for k,v in model.encoder.state_dict().items()}
    best=validation_loss();best_state=copy.deepcopy(model.state_dict());stale=0
    steps=min(32,max(1,max(int(np.ceil(len(tensors[k]['train'][0])/batch_size)) for k in real_keys)))
    for epoch in range(epochs):
        model.train()
        for _ in range(steps):
            opt.zero_grad();losses=[]
            for key in real_keys:
                x,y=tensors[key]['train'];ids=torch.randint(len(x),(min(batch_size,len(x)),),generator=gen)
                with torch.no_grad():local=model.local(key,x[ids])
                residual=model.residual(key,x[ids])
                losses.append(F.binary_cross_entropy_with_logits(local+residual,y[ids])+.01*residual.square().mean())
            loss=torch.stack(losses).mean();loss.backward();torch.nn.utils.clip_grad_norm_(trainable,5.);opt.step()
        value=validation_loss()
        if not np.isfinite(value):raise ValueError('Nonfinite shared loss')
        history['shared'].append({'epoch':epoch+1,'validation_macro_bce':value})
        print('Shared neural encoder epoch',epoch+1,'validation BCE',round(value,5),flush=True)
        if value<best-1e-5:best=value;best_state=copy.deepcopy(model.state_dict());stale=0
        else:stale+=1
        if stale>=patience:break
    model.load_state_dict(best_state);model.eval();policies={}
    for key,d in data.items():
        v=d['split']=='validation';x=cache[key][v]
        local=probabilities(model,key,x,0.);joint=probabilities(model,key,x,1.) if key in real_keys else local
        policy=choose_policy(d['y'][v],local,joint,fpr)
        if d['adapter']['synthetic']:policy['shared_weight']=0.
        policies[key]=policy
    change=float(np.sqrt(sum(float((v-initial_encoder[k]).square().sum()) for k,v in model.encoder.state_dict().items())))
    bundle={'format':FORMAT,'dimensions':dimensions,'real_processes':real_keys,'weights':json_weights(model),
            'adapters':{k:d['adapter'] for k,d in data.items()},'processes':policies,
            'shared_encoder_weight_change_l2':change,'synthetic_shared_training':False,
            'regression_gate':{'passed':False,'failures':['Final test evaluation not performed']}}
    failures=[key+': insufficient validation discrimination' for key,p in policies.items()
              if p['validation']['auc']<.6 or p['validation']['recall']<=0]
    bundle['validation_gate']={'passed':not failures,'failures':failures}
    validate_bundle(bundle)
    (out/'candidate.json').write_text(json.dumps(bundle,indent=2),encoding='utf-8')
    history['shared_encoder_weight_change_l2']=change
    (out/'training.json').write_text(json.dumps(history,indent=2),encoding='utf-8')
    snapshot={'candidate_sha256':file_hash(out/'candidate.json'),'cache':{k:file_hash(out/(k+'.npz')) for k in data}}
    (out/'frozen.json').write_text(json.dumps(snapshot,indent=2),encoding='utf-8')
    lines=['# MILG 신경망 학습·검증','',f'공유 인코더 가중치 변화 L2: {change:.6f}',
           '', '| 공정 | 검증 AUC | 검출률 | 정상 오경보 | 공유 비중 |','|---|---|---|---|---|']
    for key,p in policies.items():
        m=p['validation'];lines.append(f"| {key} | {m['auc']:.4f} | {m['recall']:.2%} | {m['false_positive_rate']:.2%} | {p['shared_weight']:.2f} |")
    lines+=['',f"검증 기준 통과: {not failures}",'최종 시험은 채점하지 않았습니다. 기존 운영 모델은 변경하지 않았습니다.']
    lines+=['- '+reason for reason in failures]
    (out/'validation.md').write_text('\n'.join(lines),encoding='utf-8')
    return bundle

def file_hash(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for b in iter(lambda:f.read(8*1024*1024),b''):h.update(b)
    return h.hexdigest()

def validation_gate_of(bundle):
    failures=[key+': insufficient validation discrimination' for key,p in bundle['processes'].items()
              if p['validation']['auc'] is None or p['validation']['auc']<.6 or p['validation']['recall']<=0]
    return {'passed':not failures,'failures':failures}

def evaluate(directory):
    from .joint_engine import regression_gate
    import torch
    torch.set_num_threads(4)
    out=Path(directory)
    if (out/'evaluation.json').exists():raise ValueError('This frozen candidate was already evaluated; read evaluation.json instead of rescoring.')
    frozen=json.loads((out/'frozen.json').read_text(encoding='utf-8'))
    if file_hash(out/'candidate.json')!=frozen['candidate_sha256']:raise ValueError('Frozen candidate changed')
    bundle=json.loads((out/'candidate.json').read_text(encoding='utf-8'))
    validation_gate=validation_gate_of(bundle)
    if not validation_gate['passed']:
        report={'status':'blocked_before_test','test_scored':False,'gate':validation_gate,
                'frozen_candidate_sha256':frozen['candidate_sha256']}
        (out/'evaluation.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
        print('Validation gate failed. Final test kept unscored. Read validation.md; existing engine retained.',flush=True)
        return report
    model=restore(bundle);reference={};candidate={}
    for key,policy in bundle['processes'].items():
        if file_hash(out/(key+'.npz'))!=frozen['cache'][key]:raise ValueError('Frozen evaluation data changed')
        with np.load(out/(key+'.npz'),allow_pickle=False) as cache:
            t=cache['split']=='test';x=cache['inputs'][t];y=cache['target'][t];groups=cache['groups'][t]
        local=probabilities(model,key,x,0.);score=probabilities(model,key,x,policy['shared_weight'])
        reference[key]={'validation':policy['local_validation'],'test':metrics(y,local,policy['local_threshold'])}
        candidate[key]={'validation':policy['validation'],'test':metrics(y,score,policy['threshold']),
                        'test_by_group':{str(g):metrics(y[groups==g],score[groups==g],policy['threshold']) for g in sorted(set(groups))}}
    gate=regression_gate(reference,candidate)
    for key,policy in bundle['processes'].items():
        if policy['validation']['auc']<.6 or policy['validation']['recall']<=0:
            gate['failures'].append(key+': insufficient validation discrimination')
    gate['passed']=not gate['failures']
    report={'test_scored':True,'reference_private_neural':reference,'shared_private_neural':candidate,'gate':gate,
            'frozen_candidate_sha256':frozen['candidate_sha256'],'shared_weights':{k:p['shared_weight'] for k,p in bundle['processes'].items()},
            'limits':'Old public test data, already inspected in previous experiments; not fresh blind validation. Reference uses full process features and the same split, not historical saved model metrics. No test-based threshold or mixture tuning.'}
    (out/'evaluation.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    if gate['passed']:
        deploy=copy.deepcopy(bundle);deploy['regression_gate']=gate;deploy['parent_candidate_sha256']=frozen['candidate_sha256']
        (out/'deployment.json').write_text(json.dumps(deploy,indent=2),encoding='utf-8')
    lines=['# MILG 공유 인코더 + 공정 전용 신경망 평가','', '| 공정 | 전용 AUC | 공유 보완 AUC | 검출률 | 정상 오경보 | 공유 비중 |','|---|---|---|---|---|---|']
    for key in candidate:
        a=reference[key]['test'];b=candidate[key]['test'];alpha=bundle['processes'][key]['shared_weight']
        lines.append(f"| {key} | {a['auc']:.4f} | {b['auc']:.4f} | {b['recall']:.2%} | {b['false_positive_rate']:.2%} | {alpha:.2f} |")
    lines+=['',f"등록 기준 통과: {gate['passed']}",'공유 비중 0은 검증 데이터에서 선택한 공정 전용 모델 유지입니다.',
            '합성 압입은 공유 신경망 학습에서 제외했습니다. 현재 저장된 운영 모델보다 우수하다는 증명은 아닙니다.',
            '이전부터 확인한 공개 시험 데이터입니다. 새로운 블라인드 검증이 아닙니다.']
    lines+=['- '+r for r in gate['failures']]
    (out/'evaluation.md').write_text('\n'.join(lines),encoding='utf-8');print('\n'.join(lines),flush=True)
    return report

def install(directory,state,by):
    from .routing import Router
    path=Path(directory)/'deployment.json'
    if not path.exists():raise ValueError('No passing deployment artifact; existing engine retained')
    bundle=json.loads(path.read_text(encoding='utf-8'));validate_bundle(bundle)
    router=Router(state);previous=router.routes()
    try:
        for key in bundle['processes']:router.register(key,path,'joint_v2',by)
    except Exception:
        router.registry.write_text(json.dumps(previous,indent=2),encoding='utf-8');raise
    (Path(state)/'routes.previous.json').write_text(json.dumps(previous,indent=2),encoding='utf-8')
    print('Installed neural shared/private engine routes:',list(bundle['processes']))

def main():
    parser=argparse.ArgumentParser(description=__doc__);sub=parser.add_subparsers(dest='action',required=True)
    p=sub.add_parser('train')
    for k in ('mct-dir','screw-dir','extra-dir','out'):p.add_argument('--'+k,required=True)
    p.add_argument('--epochs',type=int,default=40);p.add_argument('--patience',type=int,default=6);p.add_argument('--threads',type=int,default=4)
    p.add_argument('--max-rows',type=int,default=100000);p.add_argument('--target-fpr',type=float,default=.05)
    p=sub.add_parser('evaluate');p.add_argument('--run',required=True)
    p=sub.add_parser('install');p.add_argument('--run',required=True);p.add_argument('--state',required=True);p.add_argument('--by',required=True)
    a=parser.parse_args()
    if a.action=='train':
        from .joint_engine import load_benchmarks
        if min(a.epochs,a.patience,a.threads)<1 or a.max_rows<1000 or not 0<a.target_fpr<1:parser.error('Invalid training configuration')
        out=Path(a.out)/datetime.now().strftime('%Y%m%d-%H%M%S-%f');out.mkdir(parents=True)
        data,manifest=load_benchmarks(a.mct_dir,a.screw_dir,a.extra_dir,a.max_rows,screw_v2=True)
        bundle=train(data,out,a.epochs,a.patience,threads=a.threads,fpr=a.target_fpr)
        manifest.update(architecture=FORMAT,test_scored=False,epochs_max=a.epochs,patience=a.patience,
                        shared_encoder_weight_change_l2=bundle['shared_encoder_weight_change_l2'])
        (out/'manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
        print('Neural training complete. Final test has not been scored. Saved:',out.resolve(),flush=True)
    elif a.action=='evaluate':evaluate(a.run)
    else:install(a.run,a.state,a.by)

if __name__=='__main__':main()
