from __future__ import annotations
import argparse
import asyncio
from config_loader import load_system
from workflow_db import ROOT,record_source_health,upsert_job
from screening import run_l1
from ai_fit import run_l2
from boss_schema import completeness,detail_status
from boss_parser import canonical_job_url,parse_boss_body

SYSTEM=load_system();RADAR=SYSTEM['radar'];CDP=RADAR['cdp_endpoint'];POLL=float(RADAR.get('local_poll_seconds',0.5));SLOW=int(RADAR.get('playwright_slow_mo_ms',500));RADAR_URLS=ROOT/'radar_urls.txt'
SECURITY_MARKERS=('/web/passport/zp/security','/web/user/safe/verify','/web/passport/zp/verify')
CARD_JS=r'''() => {const out=[],seen=new Set(),txt=n=>n&&n.innerText?n.innerText.trim():'';for(const a of document.querySelectorAll('a[href*="/job_detail/"]')){if(!a.href||seen.has(a.href))continue;seen.add(a.href);const e=a.closest('li,.job-card-wrapper,.job-card-box,[class*="job-card"]')||a.parentElement;if(!e)continue;const pick=ss=>{for(const s of ss){const n=e.querySelector(s);if(txt(n))return txt(n)}return ''};out.push({detail_url:a.href,title:pick(['.job-name','.job-title','[class*="job-name"]','[class*="job-title"]'])||txt(a),company:pick(['.company-name','[class*="company-name"]']),location:pick(['.job-area','.job-location','[class*="job-area"]','[class*="location"]']),salary:pick(['.salary','.job-salary','[class*="salary"]']),card_text:txt(e).replace(/\s+/g,' ').slice(0,1800)})}return out.slice(0,200)}'''

async def extract_cards_stable(page,retries=6):
    """BOSS搜索页会二次导航；按0.5秒节奏重试卡片抽取。"""
    last_error=''
    for _ in range(retries):
        try:
            cards=await page.evaluate(CARD_JS)
            if cards:return cards
        except Exception as e:last_error=str(e)
        await page.wait_for_timeout(500)
    if last_error:record_source_health('boss_active_radar','error',page.url,f'card extraction retries exhausted: {last_error[:300]}')
    return []

def security(url):return any(x in (url or '') for x in SECURITY_MARKERS)
def page_kind(url):
    u=url or ''
    if 'zhipin.com' not in u:return 'ignore'
    if any(x in u for x in ['/web/geek/resume','/web/geek/chat','/web/geek/account']):return 'ignore'
    if '/job_detail/' in u:return 'detail'
    if '/web/geek/jobs' in u or '/zhaopin/' in u:return 'list'
    return 'ignore'

async def wait_detail_settle(page,max_checks=6):
    """以0.5秒为浏览动作节奏，按正文状态判断就绪。"""
    last_len=-1
    for _ in range(max_checks):
        try:n=await page.locator('body').evaluate("el => (el.innerText || '').length")
        except Exception:n=0
        if n>=500 and n==last_len:return
        last_len=n
        await page.wait_for_timeout(500)

async def save_detail(page,card=None,source_url=''):
    if security(page.url):
        record_source_health('boss_9227','blocked_security',page.url,'详情页触发安全校验；不绕过。');return ''
    body=await page.locator('body').inner_text(timeout=7000)
    login_required=('登录查看完整内容' in body) or ('我要招聘 我要找工作 登录/注册' in body)
    job=parse_boss_body(body,page.url,card);job.update(source='boss_9227',source_url=source_url or page.url)
    raw_hash=job.pop('raw_page_sha256','')
    job['source_evidence']={'capture_method':'Playwright CDP 9227 BOSS page','boss_primary_source':True,'detail_opened':True,'logged_in':not login_required,'slow_mo_ms':SLOW,'no_stealth':True,'no_security_bypass':True,'page_text_sha256':raw_hash}
    if login_required:
        job['l0_detail_status']='needs_login';jid=upsert_job(job)
        record_source_health('boss_9227','needs_login',job.get('detail_url',page.url),'BOSS当前未登录或详情被登录墙截断；需要用户在9227完成登录，不绕过。')
        print(f"[L0 needs_login] {job.get('company','')} | {job.get('title','')}")
        return jid
    comp=completeness(job);status=detail_status(job);job['l0_detail_status']=status;jid=upsert_job(job)
    if status=='detail_complete':
        run_l1(jid);run_l2(jid);record_source_health('boss_9227','ok',job['detail_url'],f"完整BOSS JD completeness={comp['score']}");print(f"[L0 complete] {job['company']} | {job['title']} | {comp['score']}")
    else:
        record_source_health('boss_9227','needs_detail',job.get('detail_url',page.url),str(comp));print(f"[L0 needs_detail] {job.get('company','')} | {job.get('title','')} | {comp}")
    return jid

async def scrape_detail(context,card,source_url):
    target=canonical_job_url(card.get('detail_url',''));page=await context.new_page()
    try:
        await page.goto(target,wait_until='domcontentloaded',timeout=15000)
        if page.url=='about:blank':raise RuntimeError('CDP detail page remained about:blank')
        if security(page.url):
            card.update(source='boss_9227',source_url=source_url,detail_url=target,l0_detail_status='blocked_security',source_evidence={'card_seen':True,'detail_security_gate':True});jid=upsert_job(card);record_source_health('boss_9227','blocked_security',page.url,'职位保留L0 needs_detail');return jid
        await wait_detail_settle(page);return await save_detail(page,card,source_url)
    except Exception as e:
        card.update(source='boss_9227',source_url=source_url,detail_url=target,l0_detail_status='needs_detail',source_evidence={'card_seen':True,'detail_error':str(e)[:300]});jid=upsert_job(card);record_source_health('boss_9227','error',target,str(e)[:500]);return jid
    finally:
        try:await asyncio.wait_for(page.close(),timeout=3)
        except Exception:pass

async def capture_cards(context,page,seen):
    if security(page.url):record_source_health('boss_9227','blocked_security',page.url,'职位列表安全校验');return
    cards=await extract_cards_stable(page)
    if not cards:return
    for card in cards:
        url=canonical_job_url(card.get('detail_url',''))
        if not url or url in seen:continue
        seen.add(url);card['detail_url']=url;await scrape_detail(context,card,page.url)

async def passive_loop(context):
    seen_cards=set();seen_details=set()
    while True:
        for page in list(context.pages):
            kind=page_kind(page.url)
            if kind=='detail':
                u=canonical_job_url(page.url)
                if u not in seen_details:
                    seen_details.add(u)
                    try:await save_detail(page,None,page.url)
                    except Exception as e:record_source_health('boss_9227','error',page.url,f'open detail capture: {e}')
            elif kind=='list':await capture_cards(context,page,seen_cards)
        await asyncio.sleep(POLL)

async def active_loop(context):
    interval=max(30,int(RADAR.get('active_scan_interval_seconds',300)));page=await context.new_page();seen=set()
    while True:
        urls=[x.strip() for x in RADAR_URLS.read_text(encoding='utf-8-sig').splitlines() if x.strip() and not x.lstrip().startswith('#')] if RADAR_URLS.exists() else []
        for url in urls:
            try:
                await page.goto(url,wait_until='domcontentloaded',timeout=15000)
                if page.url=='about:blank':record_source_health('boss_active_radar','error',url,'radar tab about:blank');continue
                if security(page.url):record_source_health('boss_active_radar','blocked_security',page.url,f'来源 {url}');continue
                await page.wait_for_timeout(500)
                kind=page_kind(page.url)
                if kind=='detail':await save_detail(page,None,url)
                elif kind=='list':await capture_cards(context,page,seen)
                record_source_health('boss_active_radar','ok',url,'radar page scanned')
            except Exception as e:record_source_health('boss_active_radar','error',url,str(e)[:500])
        await asyncio.sleep(interval)

async def scan_once(context,max_jobs=30,radar_file=RADAR_URLS):
    seen=set();captured=[];captured_set=set()
    # 先采用户当前已打开的真实BOSS详情/广州搜索页；忽略简历、聊天、账户页。
    for page in list(context.pages):
        if len(captured)>=max_jobs:break
        kind=page_kind(page.url)
        if kind=='detail':
            try:
                jid=await save_detail(page,None,page.url)
                if jid and jid not in captured_set:captured_set.add(jid);captured.append(jid)
            except Exception as e:record_source_health('boss_9227','error',page.url,f'once open-detail: {e}')
        elif kind=='list':
            cards=await extract_cards_stable(page)
            if not cards:continue
            for card in cards:
                if len(captured)>=max_jobs:break
                url=canonical_job_url(card.get('detail_url',''))
                if not url or url in seen:continue
                seen.add(url);card['detail_url']=url
                jid=await scrape_detail(context,card,page.url)
                if jid and jid not in captured_set:captured_set.add(jid);captured.append(jid)
    # 若当前tab不足，再按radar_urls补齐；总量由max_jobs硬限制。
    if len(captured)<max_jobs and radar_file.exists():
        page=await context.new_page()
        try:
            urls=[x.strip() for x in radar_file.read_text(encoding='utf-8-sig').splitlines() if x.strip() and not x.lstrip().startswith('#')]
            for source_url in urls:
                if len(captured)>=max_jobs:break
                try:
                    await page.goto(source_url,wait_until='domcontentloaded',timeout=15000);await page.wait_for_timeout(500)
                    if security(page.url):record_source_health('boss_active_radar','blocked_security',page.url,f'once来源 {source_url}');continue
                    if page_kind(page.url)!='list':continue
                    cards=await extract_cards_stable(page)
                    for card in cards:
                        if len(captured)>=max_jobs:break
                        url=canonical_job_url(card.get('detail_url',''))
                        if not url or url in seen:continue
                        seen.add(url);card['detail_url']=url
                        jid=await scrape_detail(context,card,source_url)
                        if jid and jid not in captured_set:captured_set.add(jid);captured.append(jid)
                except Exception as e:record_source_health('boss_active_radar','error',source_url,f'once: {e}')
        finally:await page.close()
    unique=list(dict.fromkeys(captured));print(f'[ONCE DONE] captured={len(unique)} max_jobs={max_jobs}');return unique

async def main():
    ap=argparse.ArgumentParser(description='BOSS 9227 capture worker')
    ap.add_argument('--once',action='store_true',help='只扫描一轮后退出，用于验收/手动运行')
    ap.add_argument('--max-jobs',type=int,default=30,help='--once最多处理岗位数，默认30')
    ap.add_argument('--detail-url',action='append',default=[],help='定向重抓一个BOSS /job_detail/ URL；可重复传入')
    ap.add_argument('--detail-file',default='',help='从文件逐行读取BOSS /job_detail/ URL；相对路径从job-agent目录解析')
    ap.add_argument('--radar-file',default='',help='--once使用指定radar文件；相对路径从job-agent目录解析')
    args=ap.parse_args()
    if args.detail_file:
        pth=ROOT/args.detail_file
        if pth.exists():args.detail_url.extend([x.strip() for x in pth.read_text(encoding='utf-8-sig').splitlines() if x.strip() and not x.lstrip().startswith('#')])
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        try:browser=await p.chromium.connect_over_cdp(CDP,slow_mo=SLOW,timeout=15000)
        except Exception as e:raise SystemExit(f'9227浏览器未就绪：{e}')
        if not browser.contexts:raise SystemExit('9227已连接但没有 browser context')
        context=browser.contexts[0];print(f'Job Agent v2 CDP={CDP} poll={POLL}s slow_mo={SLOW}ms');print('BOSS full-detail primary=ON; job-page whitelist=ON; security bypass=OFF; L6 unsupervised chat=OFF')
        if args.detail_url:
            page=await context.new_page(); captured=[]
            try:
                for raw_url in args.detail_url:
                    url=canonical_job_url(raw_url)
                    if not url or '/job_detail/' not in url: continue
                    try:
                        await page.goto(url,wait_until='domcontentloaded',timeout=15000); await wait_detail_settle(page)
                        jid=await save_detail(page,None,url)
                        if jid: captured.append(jid)
                    except Exception as e: record_source_health('boss_9227','error',url,f'direct detail: {e}')
            finally: await page.close()
            print(f'[DIRECT DONE] captured={len(list(dict.fromkeys(captured)))} requested={len(args.detail_url)}'); return
        if args.once:
            radar_file=(ROOT/args.radar_file) if args.radar_file else RADAR_URLS
            await scan_once(context,max(1,min(args.max_jobs,100)),radar_file=radar_file);return
        await asyncio.gather(passive_loop(context),active_loop(context))
if __name__=='__main__':asyncio.run(main())
