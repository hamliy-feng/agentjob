from __future__ import annotations
import json
from workflow_db import ROOT,connect,mark_stage
from config_loader import load_candidate,load_preferences
AI_DIR=ROOT/'data'/'ai';AI_DIR.mkdir(parents=True,exist_ok=True)
def build_l2_request(job_id):
 c=connect();job=c.execute('select * from jobs where job_id=?',(job_id,)).fetchone();l1=c.execute("select * from stage_runs where job_id=? and stage='L1'",(job_id,)).fetchone()
 if not job:c.close();raise KeyError(job_id)
 if job['l0_detail_status']!='detail_complete':c.close();raise RuntimeError('L0不是BOSS完整JD')
 if not l1 or l1['status']!='pass':c.close();raise RuntimeError('L1未通过')
 out={'schema_version':'2.1','job_id':job_id,'candidate':load_candidate(),'preferences':load_preferences(),'job':json.loads(job['raw_json'] or '{}'),'l1':json.loads(l1['output_json'] or '{}'),'instructions':['只依据真实资料、求职要求和BOSS完整JD判断，不得虚构','输出score、verdict(pass/watch/reject)、fit_summary、strengths、gaps、risks、questions、resume_focus','不得放宽L1硬条件']};c.close();return out
def save_l2_request(job_id):
 p=AI_DIR/f'request_{job_id}.json';p.write_text(json.dumps(build_l2_request(job_id),ensure_ascii=False,indent=2),encoding='utf-8');return p
def ingest_l2_review(payload,agent_id='external_ai_agent'):
 jid=payload['job_id'];c=connect();job=c.execute('select l0_detail_status from jobs where job_id=?',(jid,)).fetchone();l1=c.execute("select status from stage_runs where job_id=? and stage='L1'",(jid,)).fetchone()
 if not job or job['l0_detail_status']!='detail_complete':c.close();raise RuntimeError('L0未完成')
 if not l1 or l1['status']!='pass':c.close();raise RuntimeError('L1未通过')
 score=max(0,min(100,float(payload.get('score',0))));verdict=payload.get('verdict') if payload.get('verdict') in {'pass','watch','reject'} else ('pass' if score>=65 else 'watch' if score>=50 else 'reject');status='pass' if verdict=='pass' else verdict;clean={k:payload.get(k) for k in ['fit_summary','strengths','gaps','risks','questions','resume_focus']};clean.update(score=score,verdict=verdict);mark_stage(c,jid,'L2',status,version='2.1',agent_id=agent_id,score=score,summary=str(clean.get('fit_summary') or '')[:500],output=clean);c.commit();c.close();(AI_DIR/f'review_{jid}.json').write_text(json.dumps({'job_id':jid,**clean},ensure_ascii=False,indent=2),encoding='utf-8');return {'ok':True,'job_id':jid,'status':status,'score':score}
