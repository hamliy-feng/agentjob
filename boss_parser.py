from __future__ import annotations
import hashlib,re
from urllib.parse import urlsplit,urlunsplit

def canonical_job_url(url:str)->str:
    try:
        s=urlsplit(url or '')
        if '/job_detail/' in s.path:
            return urlunsplit((s.scheme or 'https',s.netloc or 'www.zhipin.com',s.path,'',''))
    except Exception: pass
    return url or ''

def _lines(text): return [x.strip() for x in (text or '').replace('\r','').split('\n') if x.strip()]
def _section(ls,starts,stops):
    st=next((i+1 for i,x in enumerate(ls) if any(x==s or x.startswith(s) for s in starts)),None)
    if st is None:return []
    en=next((i for i in range(st,len(ls)) if any(ls[i]==s or ls[i].startswith(s) for s in stops)),len(ls))
    return ls[st:en]
def _education(t): return next((x for x in ['博士','硕士','本科','大专','高中','中专/中技','学历不限'] if x in t),'')
def _experience(t):
    for p in [r'经验不限',r'在校/应届',r'1年以内',r'1-3年',r'3-5年',r'5-10年',r'10年以上']:
        m=re.search(p,t)
        if m:return m.group()
    return ''
def _internship(t):
    wd=mo=None
    m=re.search(r'(\d+(?:\.\d+)?)\s*天/周',t)
    if m:wd=float(m.group(1))
    m=re.search(r'(\d+(?:\.\d+)?)\s*个月',t)
    if m:mo=float(m.group(1))
    return wd,mo

def _classify_company_basic(items):
    """BOSS公司基本信息顺序会变化，按字段形态识别，避免把规模/行业错位成融资。"""
    company=items[0] if items else ''
    financing=size=industry=''
    for x in items[1:]:
        if not financing and (x in {'未融资','不需要融资','A轮','B轮','C轮','D轮及以上','天使轮','战略融资','已上市'} or '融资' in x or x.endswith('轮')):
            financing=x; continue
        if not size and re.fullmatch(r'\d+\s*-\s*\d+人|\d+人以上|0-20人|20-99人|100-499人|500-999人|1000-9999人|10000人以上',x):
            size=x; continue
        if not industry and x not in {'...','·'}:
            industry=x
    return company,financing,size,industry

def parse_boss_body(body:str,url:str,card:dict|None=None)->dict:
    card=card or {};ls=_lines(body);joined='\n'.join(ls);header=meta=''
    if '招聘中' in ls:
        i=ls.index('招聘中');header=ls[i+1] if i+1<len(ls) else '';meta=ls[i+2] if i+2<len(ls) else ''
    sr=re.compile(r'(?:\d+(?:\.\d+)?\s*[-~—至]\s*\d+(?:\.\d+)?K(?:·\d+薪)?|\d+(?:\.\d+)?\s*[-~—至]\s*\d+(?:\.\d+)?元/天|\d+(?:\.\d+)?\s*[-~—至]\s*\d+(?:\.\d+)?元/时)')
    sm=sr.search(header) or sr.search(joined[:1600]);salary=sm.group(0).replace(' ','') if sm else card.get('salary','')
    title=header.replace(salary,'').strip() if salary and salary in header else header;title=title or card.get('title','')
    location=meta.split()[0] if meta.split() else card.get('location','');education=_education(meta or joined[:1800]);experience=_experience(meta or joined[:1800]);wd,months=_internship(meta or joined[:1800])
    basic=[x for x in _section(ls,('公司基本信息',),('查看全部职位','微信扫码','职位描述')) if x not in {'...','·'}]
    company,financing,size,industry=_classify_company_basic(basic)
    company=company or card.get('company','')
    benefits=[]
    if '公司基本信息' in ls:
        bi=ls.index('公司基本信息');c=[x for x in ls[max(0,bi-8):bi] if x not in {'...','上传附件简历','完善在线简历','感兴趣 立即沟通','立即沟通'}]
        if c:
            b=c[-1];words=['五险一金','补充医疗保险','补充医疗','意外险','定期体检','年终奖','绩效奖金','保底工资','底薪加提成','股票期权','带薪年假','餐补','零食下午茶','下午茶','员工旅游','团建聚餐','节日福利','生日福利','免费工装']
            benefits=[x for x in words if x in b]
    ds=next((i for i,x in enumerate(ls) if x=='职位描述'),None);ri=qi=si=None
    if ds is not None:
        for i in range(ds+1,len(ls)):
            if ri is None and any(ls[i].startswith(x) for x in ['岗位职责','职位职责','工作职责']):ri=i;continue
            if qi is None and any(ls[i].startswith(x) for x in ['任职要求','岗位要求','职位要求']):qi=i;break
        for i in range((qi or ri or ds)+1,len(ls)):
            if ls[i]=='竞争力分析':si=i;break
    tags=[]
    if ds is not None:
        te=ri or qi or min(len(ls),ds+20);tags=[x for x in ls[ds+1:te] if len(x)<=30 and not re.match(r'^\d+[.、]',x)]
    responsibilities='\n'.join(ls[ri+1:(qi or si or len(ls))]).strip() if ri is not None else ''
    requirements='\n'.join(ls[qi+1:(si or len(ls))]).strip() if qi is not None else ''
    description='\n'.join(x for x in [responsibilities,requirements] if x).strip()
    rn=rt=ra=''
    if si is not None:
        pre=ls[max(0,si-10):si]
        for j,x in enumerate(pre):
            if any(a in x for a in ['在线','刚刚活跃','今日活跃','本周活跃','3日内活跃','近3日活跃','月内活跃']):
                ra=x;rn=pre[j-1] if j>0 else '';tail=[y for y in pre[j+1:] if y not in {company,'·'}];rt=tail[-1] if tail else '';break
    company_intro='\n'.join(_section(ls,('公司介绍',),('查看全部','工商信息'))).strip();biz=_section(ls,('工商信息',),('查看全部','工作地址'));business_info={}
    for i in range(0,len(biz)-1,2):
        if biz[i] in {'公司名称','法定代表人','成立日期','企业类型','经营状态','注册资金'}:business_info[biz[i]]=biz[i+1]
    ad=_section(ls,('工作地址',),('点击查看地图','更多职位'));address=ad[0] if ad else '';m=re.search(r'页面更新时间[:：]\s*([^\n]+)',body);updated=m.group(1).strip() if m else ''
    return {'title':title,'company':company,'salary':salary,'location':location,'experience':experience,'education':education,'tags':tags,'work_days_per_week':wd,'internship_months':months,'employment_type':'internship' if ('元/天' in salary or wd is not None or months is not None or '实习' in title) else 'full_time','responsibilities':responsibilities,'requirements':requirements,'description':description,'recruiter_name':rn,'recruiter_title':rt,'recruiter_activity':ra,'address':address,'benefits':benefits,'company_financing':financing,'company_size':size,'company_industry':industry,'company_intro':company_intro,'business_info':business_info,'page_updated_at':updated,'detail_url':canonical_job_url(url),'raw_page_text':body[:50000],'raw_page_sha256':hashlib.sha256(body.encode('utf-8','ignore')).hexdigest()}
