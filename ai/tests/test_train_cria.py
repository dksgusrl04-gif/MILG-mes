import numpy as np
import pandas as pd
from mlng_ai.train_cria import causal_features,split_episodes

def observations(n):
    return pd.DataFrame({
        'observation.state.wrench_raw_chunk':[np.full((4,6),i+1.) for i in range(n)],
        'observation.state.wrench_chunk_valid_len':[4]*n,
        'observation.state.ee_pose':[np.array([i,0,0,0,0,0,1.]) for i in range(n)]})

def test_feature_prefix_does_not_see_future_or_labels():
    df=observations(10);prefix=causal_features(df[:5])
    df['episode_label']=0;df['frame_label']=0;df['source_id']=999
    assert np.allclose(prefix,causal_features(df).iloc[:5])
    assert not any('label' in c or 'source' in c for c in prefix)

def test_left_padding_not_counted_as_measurement():
    df=observations(1)
    df.at[0,'observation.state.wrench_raw_chunk']=np.array([[1]*6,[1]*6,[2]*6,[3]*6],float)
    df.at[0,'observation.state.wrench_chunk_valid_len']=3
    assert causal_features(df).iloc[0].wrench_mean_0==2

def test_date_and_task_splits_never_share_episodes():
    df=pd.DataFrame([{'eid':i,'task':'t'+str(i%4),'date':'2026-07-'+str(i//4+1).zfill(2),'target':i%2} for i in range(40)])
    for holdout in (None,'t3'):
        s=split_episodes(df,holdout)
        a,b,c=[set(s[k]) for k in ('train','validation','test')]
        assert not a&b and not a&c and not b&c
        assert a|b|c==set(df.eid)
        if holdout:assert set(df[df.eid.isin(c)].task)=={holdout}
        else:assert set(df[df.eid.isin(a)].date).isdisjoint(set(df[df.eid.isin(c)].date))
