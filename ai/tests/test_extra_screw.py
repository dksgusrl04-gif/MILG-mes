import io
import zipfile
import numpy as np
import pytest
from mlng_ai.extra_screw import describe_curve, signal_features, audit_m2m3

def test_nonfinite_curve_rejected():
    with pytest.raises(ValueError,match='Invalid numeric'):
        describe_curve([0,1,2],[1,np.nan,3])

def test_real_axis_reversals_reported_not_silently_sorted():
    result=describe_curve([0,2,1],[1,2,3])
    assert result['angle_reversals']==1

def test_shape_retains_order_missing_from_summary_statistics():
    a=signal_features([0,1,3,2],'torque')
    b=signal_features([2,3,1,0],'torque')
    assert a['torque_mean']==b['torque_mean']
    assert a['torque_shape_0']!=b['torque_shape_0']

def test_m2m3_skips_simulated_and_calculated_sheets(tmp_path):
    import openpyxl
    book=openpyxl.Workbook();raw=book.active;raw.title='Test Data'
    raw.append(['Data for screw',None]);raw.append([None,None]);raw.append(['Screw Angle','Screw Torque'])
    for pair in [(0,1),(1,2),(2,3)]:raw.append(pair)
    simulated=book.create_sheet('Test Data (GPR)');simulated.append(['not raw'])
    data=io.BytesIO();book.save(data)
    path=tmp_path/'Datasets.zip'
    with zipfile.ZipFile(path,'w') as z:z.writestr('Datasets/M2_Screw_Dataset.xlsx',data.getvalue())
    result=audit_m2m3(path)
    assert result['raw_curves']==1
    assert result['supervised_quality_training'] is False
    assert result['excluded_sheets']==['Datasets/M2_Screw_Dataset.xlsx::Test Data (GPR)']

def test_aursad_excludes_all_duplicate_copies_even_with_conflicting_labels(tmp_path):
    import h5py
    from mlng_ai.extra_screw import aursad_features
    path=tmp_path/'tiny.h5'
    channels=['output_double_register_25']+[f'actual_TCP_force_{i}' for i in range(6)]
    first=np.arange(21,dtype=float).reshape(3,7)
    with h5py.File(path,'w') as f:
        g=f.create_group('complete_data');g['axis1']=np.arange(9)
        blocks=[([],np.empty((9,0))), (channels,np.vstack([first,first,first+1])),
                ([],np.empty((9,0))), (['sample_nr'],np.repeat([1,2,3],3).reshape(9,1))]
        for i,(cols,values) in enumerate(blocks):
            g[f'block{i}_items']=np.array(cols,dtype='S32');g[f'block{i}_values']=values
    result=aursad_features(path,[{'sample_nr':1,'label':0},{'sample_nr':2,'label':1},{'sample_nr':3,'label':1}])
    assert result.run.tolist()==[3]
    assert result.attrs['cleaning']['excluded_nonunique_operations']==2
    assert result.attrs['cleaning']['binary_label_conflicting_groups_for_audit_only']==1
