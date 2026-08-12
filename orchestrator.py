from workflow_db import connect,mark_stage
from screening import run_l1
from ai_fit import run_l2
from due_diligence import attach_cached_company_report,save_request
from materials import generate_l4

def advance_job(jid):
 out={'job_id':jid,'actions':[]};c=connect();j=c.execute('select * from jobs where job_id=?',(jid,)).fetchone();c.close()
 if not j:raise KeyError(jid)
 if j['l0_detail_status']!='detail_complete':out['blocked']='L0 needs complete BOSS JD';return out
 c=connect();s=c.execute("select status from stage_runs where job_id=? and stage='L1'",(jid,)).fetchone();c.close()
 if not s or s['status'] not in {'pass','reject'}:
  r=run_l1(jid);out['actions'].append({'L1':r})
  if r.get('status')!='pass':return out
 c=connect();s=c.execute("select status from stage_runs where job_id=? and stage='L2'",(jid,)).fetchone();c.close()
 if not s or s['status'] in {'needs_ai','needs_detail','error','blocked'}:out['actions'].append({'L2':run_l2(jid)})
 c=connect();s=c.execute("select status from stage_runs where job_id=? and stage='L2'",(jid,)).fetchone();c.close()
 if not s or s['status']!='pass':return out
 if attach_cached_company_report(jid):out['actions'].append({'L3':'cached'})
 else:
  p=save_request(jid);c=connect();mark_stage(c,jid,'L3','queued',version='2.1',agent_id='orchestrator',summary='等待Research Agent背调',output={'request_path':str(p)});c.commit();c.close();out['actions'].append({'L3_request':str(p)});return out
 c=connect();s=c.execute("select status from stage_runs where job_id=? and stage='L3'",(jid,)).fetchone();c.close()
 if s and s['status']=='complete':out['actions'].append({'L4':generate_l4(jid)})
 return out

def advance_all():
 c=connect();ids=[r[0] for r in c.execute("select job_id from jobs where l0_detail_status='detail_complete' order by last_seen desc")];c.close();return [advance_job(x) for x in ids]
