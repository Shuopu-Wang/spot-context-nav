"""Recompute exploratory image-space metrics from pseudonymous reference data.
Run: python code/analyze.py --root .
Dependencies: Python 3, numpy. No external API calls.
"""
import argparse, collections, csv, hashlib, itertools, json, math
from pathlib import Path
import numpy as np

def frechet(p,q):
    """Discrete Frechet distance; inputs are in image-diagonal units."""
    ds=np.linalg.norm(p[:,None,:]-q[None,:,:],axis=2)
    ca=np.empty(ds.shape)
    for i in range(len(p)):
        for j in range(len(q)):
            prev=min(ca[i-1,j],ca[i-1,j-1],ca[i,j-1]) if i and j else (ca[i-1,j] if i else (ca[i,j-1] if j else 0.0))
            ca[i,j]=max(ds[i,j],prev)
    return float(ca[-1,-1])

def dense_curve(record, per_segment=256):
    p=np.array(record['points'],float)
    if len(p)<2:return np.empty((0,2))
    result=[]
    for i,s in enumerate(record.get('segments',[])):
        t=np.linspace(0,1,per_segment)[:,None]
        if s['type']=='curve':
            c1=np.array(s['c1']);c2=np.array(s['c2'])
            q=(1-t)**3*p[i]+3*(1-t)**2*t*c1+3*(1-t)*t*t*c2+t**3*p[i+1]
        else:q=(1-t)*p[i]+t*p[i+1]
        result.extend(q if i==0 else q[1:])
    return np.array(result)

def resample(p,scale,n=64):
    p=np.array(p,float);q=p*scale
    ds=np.linalg.norm(np.diff(q,axis=0),axis=1)
    ar=np.r_[0,np.cumsum(ds)];unique=np.r_[True,np.diff(ar)>1e-12]
    ar=ar[unique];p=p[unique]
    if ar[-1]==0:return np.repeat(p[:1],n,axis=0)
    a=np.linspace(0,ar[-1],n)
    return np.column_stack([np.interp(a,ar,p[:,j]) for j in range(2)])

def mean(x):return float(np.mean(x)) if len(x) else None

def med(x):return float(np.median(x)) if len(x) else None

def write_csv(path,rows):
    if not rows:return
    keys=list(dict.fromkeys(k for r in rows for k in r))
    with path.open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=keys);w.writeheader();w.writerows(rows)

def main(root):
    hd=json.loads((root/'data/human_reference.json').read_text())
    pd=json.loads((root/'data/ai_proposals.json').read_text())
    props={p['image_id']:p for p in pd['proposals']}
    anns=hd['annotations'];out=[];ah=[];hh=[];audits=[];enriched=[];repro=[]
    mode_tau=hd['settings']['cluster_threshold']
    for im in hd['images']:
        iid=im['id'];p=props[iid];w,h=im['width'],im['height'];scale=np.array([w,h])/math.hypot(w,h)
        aa=[a for a in anns if a['image_id']==iid and not a['excluded']]
        hp=[a for a in aa if a['decision']=='path']
        curves={a['code']:np.array(a['curve_64'])*scale for a in hp}
        good={}
        for a in hp:
            c=np.array(a['curve_64']);se=float(np.linalg.norm((c[0]-im['start'])*scale));ge=float(np.linalg.norm((c[-1]-im['goal'])*scale))
            good[a['code']]=max(se,ge)<=.01
            if not good[a['code']]:audits.append({'index':im['index'],'image_id':iid,'image_title':im['title'],'participant_code':a['code'],'issue':'endpoint mismatch > 1% image diagonal','start_error_diagonal':se,'goal_error_diagonal':ge,'goal_error_pixels':ge*math.hypot(w,h),'possible_explanation':'Edited goal or drawing not reaching current goal; cause not established.'})
        localhh=[]
        for a,b in itertools.combinations(hp,2):
            d=frechet(curves[a['code']],curves[b['code']]);r={'index':im['index'],'image_id':iid,'image_title':im['title'],'human_1':a['code'],'human_2':b['code'],'frechet_diagonal':d,'similarity_at_tau_0_10':1/(1+d/mode_tau),'both_current_goal_consistent':good[a['code']] and good[b['code']],'neither_export_outlier':not a['outlier'] and not b['outlier']}
            hh.append(r);localhh.append(r)
        exp_vals=[r['frechet_diagonal'] for r in localhh if r['neither_export_outlier']]
        if exp_vals:repro.append(abs(mean(exp_vals)-im['summary']['mean_pairwise_frechet']))
        nc=collections.Counter(n for a in aa for n in a['norms'])
        r={'index':im['index'],'image_id':iid,'folder':im['folder'],'image_title':im['title'],'ai_decision':p['decision'],'ai_confidence_label':p['confidence_label'],'ai_norms':';'.join(p['norms']),'n_human_responses':len(aa),'n_human_paths':len(hp),'n_human_wait':len(aa)-len(hp),'human_wait_share':(len(aa)-len(hp))/len(aa),'human_human_mean_frechet':mean([v['frechet_diagonal'] for v in localhh]),'human_human_median_frechet':med([v['frechet_diagonal'] for v in localhh]),'export_route_modes':im['summary']['n_route_modes'],'export_substantial_choices':im['summary']['n_substantial_choices'],'export_top_choice_share':im['summary']['top_choice_share'],'export_entropy_bits':im['summary']['choice_entropy_bits'],'export_ambiguous':im['summary']['ambiguous'],'endpoint_mismatch_paths':sum(not x for x in good.values()),'export_outliers':im['summary']['n_outliers'],'ai_human_action_matches':sum(a['decision']==p['decision'] for a in aa),'ai_human_action_agreement':mean([a['decision']==p['decision'] for a in aa]),'dominant_action_agreement':p['decision']==('path' if len(hp)>=len(aa)-len(hp) else 'wait'),'human_norm_counts':json.dumps(nc,sort_keys=True)}
        ext=dict(p)
        if p['decision']=='path':
            dense=dense_curve(p);curve=resample(dense,scale);pc=curve*scale
            if not np.all((dense>=0)&(dense<=1)):raise ValueError('Proposal leaves the image')
            if not np.allclose(curve[0],im['start']) or not np.allclose(curve[-1],im['goal']):raise ValueError('Proposal endpoint error')
            line=np.linspace(im['start'],im['goal'],64)*scale
            ext['curve_64']=np.round(curve,8).tolist()
            rows=[]
            for a in hp:
                hc=curves[a['code']];dd=frechet(pc,hc);ld=frechet(line,hc)
                union=set(p['norms'])|set(a['norms']);inter=set(p['norms'])&set(a['norms'])
                z={'index':im['index'],'image_id':iid,'image_title':im['title'],'human_code':a['code'],'human_route':a['route'],'human_export_outlier':a['outlier'],'human_compiled_trimmed':a['trimmed'],'human_current_goal_consistent':good[a['code']],'ai_frechet_diagonal':dd,'ai_frechet_pixels':dd*math.hypot(w,h),'ai_rmse_diagonal':float(np.sqrt(np.mean(np.sum((pc-hc)**2,axis=1)))),'ai_similarity_at_tau_0_10':1/(1+dd/mode_tau),'straight_line_frechet_diagonal':ld,'ai_minus_straight_frechet':dd-ld,'norm_jaccard':len(inter)/len(union) if union else 1.0}
                rows.append(z);ah.append(z)
            def vals(key,fltr=lambda x:True):return [x[key] for x in rows if fltr(x)]
            r.update({'ai_mean_frechet':mean(vals('ai_frechet_diagonal')),'ai_median_frechet':med(vals('ai_frechet_diagonal')),'ai_nearest_human_frechet':min(vals('ai_frechet_diagonal')),'ai_mean_rmse':mean(vals('ai_rmse_diagonal')),'ai_mean_similarity_tau_0_10':mean(vals('ai_similarity_at_tau_0_10')),'straight_mean_frechet':mean(vals('straight_line_frechet_diagonal')),'ai_mean_frechet_current_goal_only':mean(vals('ai_frechet_diagonal',lambda x:x['human_current_goal_consistent'])),'straight_mean_frechet_current_goal_only':mean(vals('straight_line_frechet_diagonal',lambda x:x['human_current_goal_consistent'])),'ai_mean_frechet_excluding_export_outliers':mean(vals('ai_frechet_diagonal',lambda x:not x['human_export_outlier'])),'ai_mean_frechet_goal_consistent_nonoutliers':mean(vals('ai_frechet_diagonal',lambda x:x['human_current_goal_consistent'] and not x['human_export_outlier'])),'ai_mean_norm_jaccard':mean(vals('norm_jaccard')),'ai_frechet_to_compiled':frechet(pc,np.array(im['compiled']['curve'])*scale)})
            routes=[]
            for rt in im['routes']:
                if rt['medoid'] in curves:routes.append((frechet(pc,curves[rt['medoid']]),rt['route'],rt['count']))
            routes.sort();closest=routes[0] if routes else None
            maxcount=max([rt['count'] for rt in im['routes']]+[im['summary']['n_wait']])
            r.update({'nearest_export_route':closest[1] if closest else None,'distance_to_nearest_route_medoid':closest[0] if closest else None,'nearest_route_is_modal_at_tau_0_10':bool(closest and closest[0]<=mode_tau and closest[2]==maxcount)})
        else:
            ext['curve_64']=[]
            r['nearest_route_is_modal_at_tau_0_10']=im['summary']['n_wait']>=max([rt['count'] for rt in im['routes']]+[1])
        enriched.append(ext);out.append(r)
    macro=lambda field,where=lambda x:True:mean([x[field] for x in out if where(x) and x.get(field) is not None])
    move=[x for x in out if x['ai_decision']=='path']
    duplicates=[]
    for im in hd['images']:
        groups=collections.defaultdict(list)
        for a in anns:
            if a['image_id']==im['id'] and a['decision']=='path':groups[json.dumps(a['curve_64'])].append(a['code'])
        for g in groups.values():
            if len(g)>1:duplicates.append({'index':im['index'],'image_title':im['title'],'codes':';'.join(g),'n_identical_drawings':len(g),'interpretation':'Identical stored curve, not evidence of duplicate participants. A default straight curve can be selected independently.'})
    normcounts=collections.Counter(n for a in anns if not a['excluded'] for n in a['norms'])
    normrows=[{'norm':n['key'],'label':n['label'],'response_count':normcounts[n['key']],'response_share':normcounts[n['key']]/len(anns),'scenes_with_tag':sum(n['key'] in [v for a in anns if a['image_id']==im['id'] for v in a['norms']] for im in hd['images'])} for n in hd['settings']['norms']]
    thresholds=[]
    for tau in [.025,.05,.075,.10]:
        am=[];sm=[];hm=[]
        for im in hd['images']:
            ar=[r for r in ah if r['image_id']==im['id']]
            hr=[r for r in hh if r['image_id']==im['id']]
            if ar:
                am.append(mean([r['ai_frechet_diagonal']<=tau for r in ar]));sm.append(mean([r['straight_line_frechet_diagonal']<=tau for r in ar]))
            if hr:hm.append(mean([r['frechet_diagonal']<=tau for r in hr]))
        thresholds.append({'threshold_diagonal':tau,'threshold_pixels_at_1280x720':tau*math.hypot(1280,720),'ai_human_within_threshold_macro_31_scenes':mean(am),'straight_human_within_threshold_macro_same_31_scenes':mean(sm),'human_human_within_threshold_macro_all_39_scenes':mean(hm)})
    move_ids={r['image_id'] for r in move}
    summary={'source_export':hd['source_export'],'n_scenes':len(hd['images']),'n_responses':len(anns),'n_human_paths':sum(a['decision']=='path' for a in anns),'n_human_wait':sum(a['decision']=='wait' for a in anns),'n_contributing_participant_codes':len(set(a['code'] for a in anns)),'n_participant_accounts':hd['participant_account_count'],'role_response_counts':dict(collections.Counter(a['role'] for a in anns)),'responses_per_scene_min':min(x['n_human_responses'] for x in out),'responses_per_scene_max':max(x['n_human_responses'] for x in out),'n_preexisting_vlm_images':hd['vlm_image_count'],'n_preexisting_vlm_ratings':hd['vlm_rating_count'],'n_preexisting_candidates':hd['candidate_count'],'n_scenes_with_wait':sum(x['n_human_wait']>0 for x in out),'n_scenes_multiple_export_route_modes':sum(x['export_route_modes']>1 for x in out),'n_scenes_multiple_export_substantial_choices':sum(x['export_substantial_choices']>1 for x in out),'n_export_ambiguous_scenes':sum(x['export_ambiguous'] for x in out),'n_endpoint_mismatch_paths':len(audits),'n_export_outliers':sum(bool(a['outlier']) for a in anns),'n_compiled_trimmed':sum(bool(a['trimmed']) for a in anns),'n_empty_norm_sets':sum(not a['norms'] for a in anns),'n_identical_geometry_groups':len(duplicates),'n_drawings_in_identical_geometry_groups':sum(g['n_identical_drawings'] for g in duplicates),'n_redundant_identical_curves':sum(g['n_identical_drawings']-1 for g in duplicates),'human_mean_confidence':mean([a['confidence'] for a in anns]),'n_ai_path_proposals':len(move),'n_ai_wait_proposals':len(out)-len(move),'n_ai_human_geometry_pairs':len(ah),'n_human_human_geometry_pairs':len(hh),'human_human_mean_frechet_macro_all_39':macro('human_human_mean_frechet'),'human_human_mean_frechet_macro_same_31':macro('human_human_mean_frechet',lambda x:x['image_id'] in move_ids),'ai_human_mean_frechet_macro_31':macro('ai_mean_frechet'),'straight_human_mean_frechet_macro_same_31':macro('straight_mean_frechet'),'ai_human_mean_frechet_pooled_pairs':mean([x['ai_frechet_diagonal'] for x in ah]),'ai_human_mean_similarity_macro_31':macro('ai_mean_similarity_tau_0_10'),'ai_human_nearest_frechet_macro_31':macro('ai_nearest_human_frechet'),'ai_human_mean_rmse_macro_31':macro('ai_mean_rmse'),'ai_human_mean_frechet_goal_consistent_macro_31':macro('ai_mean_frechet_current_goal_only'),'straight_human_mean_frechet_goal_consistent_macro_31':macro('straight_mean_frechet_current_goal_only'),'ai_human_mean_frechet_nonoutliers_macro_31':macro('ai_mean_frechet_excluding_export_outliers'),'ai_human_mean_frechet_goal_consistent_nonoutliers_macro_31':macro('ai_mean_frechet_goal_consistent_nonoutliers'),'ai_less_frechet_than_straight_scene_count':sum(x['ai_mean_frechet']<x['straight_mean_frechet'] for x in move),'ai_modal_nearest_route_within_tau_move_scene_count':sum(x['nearest_route_is_modal_at_tau_0_10'] for x in move),'ai_action_agreement_response_weighted':sum(x['ai_human_action_matches'] for x in out)/len(anns),'ai_action_agreement_response_matches':sum(x['ai_human_action_matches'] for x in out),'ai_action_agreement_macro_39':macro('ai_human_action_agreement'),'ai_matches_majority_action_scenes':sum(x['dominant_action_agreement'] for x in out),'always_path_response_agreement':sum(a['decision']=='path' for a in anns)/len(anns),'max_export_frechet_reproduction_error':max(repro),'ai_proposals_sha256':hashlib.sha256((root/'data/ai_proposals.json').read_bytes()).hexdigest(),'aggregation':'Primary geometric results use all non-admin-excluded path drawings, retaining flagged outliers and endpoint mismatches. Compute mean distances to humans within each scene, then mean equally across the 31 scenes with an AI path. Wait outputs are not assigned a zero distance. Sensitivity analyses are separately named. No confidence intervals, significance tests, safety rates, or held-out claims are made.'}
    summary['relative_descriptive_frechet_reduction_vs_straight']=1-summary['ai_human_mean_frechet_macro_31']/summary['straight_human_mean_frechet_macro_same_31']
    for fname,rows in [('scene_metrics.csv',out),('ai_human_pairs.csv',ah),('human_human_pairs.csv',hh),('data_quality_flags.csv',audits),('identical_geometry_groups.csv',duplicates),('norm_frequency.csv',normrows),('threshold_sensitivity.csv',thresholds)]:write_csv(root/'data'/fname,rows)
    (root/'data/summary.json').write_text(json.dumps(summary,indent=2))
    (root/'data/ai_proposals_with_samples.json').write_text(json.dumps({**pd,'proposals':enriched},indent=2))
    print(json.dumps(summary,indent=2))
    print('\nTHRESHOLDS',json.dumps(thresholds,indent=2))
    print('\nSCENE METRICS')
    for r in out:print(r['index'],r['ai_decision'],'AI',round(r.get('ai_mean_frechet',0)*100,2),'line',round(r.get('straight_mean_frechet',0)*100,2),'modal',r['nearest_route_is_modal_at_tau_0_10'])
if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[1]);args=ap.parse_args();main(args.root)
