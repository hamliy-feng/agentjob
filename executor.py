from __future__ import annotations

import argparse
import asyncio

from config_loader import load_system
from workflow_db import connect, mark_stage, now_iso, record_source_health

SYSTEM = load_system()
RADAR = SYSTEM["radar"]
CDP = RADAR["cdp_endpoint"]
POLL = float(RADAR.get("local_poll_seconds", 0.5))
SLOW = int(RADAR.get("playwright_slow_mo_ms", 500))
SECURITY_MARKERS = ("/web/passport/zp/security", "/web/user/safe/verify", "/web/passport/zp/verify")


def security(url: str) -> bool:
    return any(x in (url or "") for x in SECURITY_MARKERS)


def pending() -> list[dict]:
    c = connect()
    rows = c.execute(
        """select g.job_id,j.detail_url,j.title,j.company
           from application_gates g join jobs j using(job_id)
           where g.confirmed=1 and g.executed=0 and g.gate_status='confirmed_pending_execution'
           order by g.updated_at"""
    ).fetchall()
    c.close()
    return [dict(r) for r in rows]


def job_item(job_id: str) -> dict | None:
    c = connect()
    row = c.execute("select job_id,detail_url,title,company,recruiter_name,content_hash from jobs where job_id=?", (job_id,)).fetchone()
    c.close()
    return dict(row) if row else None


def claim(job_id: str) -> dict | None:
    c = connect()
    try:
        c.execute("begin immediate")
        cur = c.execute(
            """update application_gates
               set gate_status='executing_initial_action',updated_at=?
               where job_id=? and confirmed=1 and executed=0 and gate_status='confirmed_pending_execution'""",
            (now_iso(), job_id),
        )
        if cur.rowcount != 1:
            c.rollback(); c.close(); return None
        row = c.execute(
            "select j.job_id,j.detail_url,j.title,j.company,j.recruiter_name,j.content_hash from jobs j where j.job_id=?", (job_id,)
        ).fetchone()
        c.commit(); c.close()
        return dict(row) if row else None
    except Exception:
        c.rollback(); c.close(); raise


def mark_executed(job_id: str, note: str) -> None:
    c = connect()
    job = c.execute("select content_hash from jobs where job_id=?", (job_id,)).fetchone()
    current_hash = (job["content_hash"] if job else "") or ""
    ts = now_iso()
    c.execute(
        """update application_gates
           set executed=1,confirmed=1,gate_status='verified_sent',confirmation_note=?,content_hash=?,verified_at=?,updated_at=?
           where job_id=?""",
        (note, current_hash, ts, ts, job_id),
    )
    c.execute(
        """insert into application_history(job_id,content_hash,status,verified_at,note) values(?,?,?,?,?)
           on conflict(job_id,content_hash) do update set status=excluded.status,verified_at=excluded.verified_at,note=excluded.note""",
        (job_id, current_hash, "verified_sent", ts, note),
    )
    ui = c.execute("select favorite from job_ui_state where job_id=?", (job_id,)).fetchone()
    fav = int(ui["favorite"] or 0) if ui else 0
    c.execute(
        """insert into job_ui_state(job_id,state,favorite,state_content_hash,note,updated_at) values(?,?,?,?,?,?)
           on conflict(job_id) do update set state='applied',favorite=excluded.favorite,state_content_hash=excluded.state_content_hash,note=excluded.note,updated_at=excluded.updated_at""",
        (job_id, "applied", fav, current_hash, "已通过9227消息/页面验证投递成功", ts),
    )
    mark_stage(
        c, job_id, "L5", "complete", version="2.1", agent_id="browser_executor",
        summary=note, output={"confirmed": True, "executed": True}
    )
    mark_stage(
        c, job_id, "L6", "blocked", version="2.1",
        summary="持续招聘者聊天默认关闭，等待人工参与", output={"unsupervised_chat": False}
    )
    c.commit(); c.close()


def mark_verification_pending(job_id: str, msg: str) -> None:
    c = connect()
    c.execute(
        """update application_gates set confirmed=1,executed=0,gate_status='verification_pending',confirmation_note=?,updated_at=? where job_id=?""",
        (msg[:1000], now_iso(), job_id),
    )
    mark_stage(c, job_id, "L5", "ready", version="2.2", agent_id="browser_executor", summary="已执行一次点击，等待9227消息侧验证；禁止再次点击", output={"confirmed": True, "executed": False, "verification_pending": True})
    c.commit(); c.close()


def mark_error(job_id: str, msg: str) -> None:
    c = connect()
    c.execute(
        """update application_gates
           set confirmed=0,executed=0,gate_status='execution_error',confirmation_note=?,updated_at=?
           where job_id=?""",
        (msg[:1000], now_iso(), job_id),
    )
    mark_stage(
        c, job_id, "L5", "error", version="2.1", agent_id="browser_executor",
        summary="L5执行失败；需要再次人工点击后才能重试", output={"confirmed": False, "executed": False}, error=msg[:1000]
    )
    c.commit(); c.close()


async def _context():
    from playwright.async_api import async_playwright
    p = await async_playwright().start()
    try:
        browser = await p.chromium.connect_over_cdp(CDP, slow_mo=SLOW, timeout=15000)
    except Exception:
        await p.stop()
        raise
    if not browser.contexts:
        await p.stop()
        raise RuntimeError("9227无浏览器context")
    return p, browser, browser.contexts[0]


async def open_job(job_id: str) -> dict:
    item = job_item(job_id)
    if not item:
        return {"ok": False, "error": "job_not_found"}
    p = browser = None
    try:
        p, browser, context = await _context()
        page = await context.new_page()
        await page.goto(item["detail_url"], wait_until="domcontentloaded", timeout=30000)
        if security(page.url):
            record_source_health("boss_l5", "blocked_security", page.url, "仅打开BOSS时遇到安全校验；未绕过。")
            return {"ok": False, "status": "blocked_security", "url": page.url}
        await page.bring_to_front()
        await page.wait_for_timeout(500)
        return {"ok": True, "status": "opened", "url": page.url}
    except Exception as e:
        return {"ok": False, "status": "error", "error": str(e)[:1000]}
    finally:
        # connect_over_cdp 复用用户现有 9227 Chrome；这里只停止 Playwright 驱动连接，绝不关闭浏览器。
        if p is not None:
            try: await p.stop()
            except Exception: pass


async def _verify_sent(context, item: dict, action_page=None) -> dict:
    """只读验证：不发送任何消息。优先看当前页状态，再看9227的BOSS消息页。"""
    signals = []
    if action_page is not None:
        try:
            await action_page.wait_for_timeout(1200)
            body = await action_page.locator("body").inner_text(timeout=4000)
            for marker in ["继续沟通", "已投递", "已申请", "沟通中", "已沟通"]:
                if marker in body:
                    signals.append(f"action_page:{marker}")
                    break
        except Exception:
            pass
    chat = next((p for p in context.pages if "/web/geek/chat" in (p.url or "")), None)
    created = False
    if chat is None:
        try:
            chat = await context.new_page(); created = True
            await chat.goto("https://www.zhipin.com/web/geek/chat", wait_until="domcontentloaded", timeout=15000)
        except Exception:
            chat = None
    if chat is not None and not security(chat.url):
        try:
            await chat.wait_for_timeout(900)
            text = await chat.locator("body").inner_text(timeout=5000)
            identifiers = [str(item.get(k) or "").strip() for k in ["company", "recruiter_name", "title"]]
            hits = [x for x in identifiers if x and x in text]
            if len(hits) >= 2 or (hits and signals):
                signals.append("boss_chat:" + "/".join(hits[:2]))
        except Exception:
            pass
        finally:
            if created:
                try: await chat.close()
                except Exception: pass
    return {"verified": bool(signals and any(x.startswith("boss_chat:") for x in signals)), "signals": signals}


async def verify_job(context, job_id: str) -> dict:
    item = job_item(job_id)
    if not item:
        return {"ok": False, "status": "job_not_found"}
    c = connect(); gate = c.execute("select * from application_gates where job_id=?", (job_id,)).fetchone(); c.close()
    if not gate or gate["gate_status"] not in {"verification_pending", "verified_sent"}:
        return {"ok": False, "status": "not_pending_verification"}
    if gate["gate_status"] == "verified_sent" or gate["executed"]:
        return {"ok": True, "status": "already_verified"}
    check = await _verify_sent(context, item)
    if check["verified"]:
        note = "9227消息侧验证成功：" + "；".join(check["signals"])
        mark_executed(job_id, note)
        return {"ok": True, "status": "verified_sent", **check}
    return {"ok": False, "status": "verification_pending", **check}


async def execute_one(context, job_id: str) -> dict:
    item = claim(job_id)
    if not item:
        return {"ok": False, "status": "not_claimed"}
    page = await context.new_page()
    try:
        await page.goto(item["detail_url"], wait_until="domcontentloaded", timeout=30000)
        if security(page.url):
            record_source_health("boss_l5", "blocked_security", page.url, "已确认岗位执行时遇到安全校验；未绕过。")
            mark_error(item["job_id"], "security verification encountered; human/browser verification required")
            return {"ok": False, "status": "blocked_security"}
        clicked = None
        for text in ["立即沟通", "立即申请", "投递简历", "申请职位", "继续沟通"]:
            loc = page.get_by_text(text, exact=True)
            if await loc.count():
                await loc.first.click(timeout=5000)
                clicked = text
                break
        if not clicked:
            mark_error(item["job_id"], "未找到支持的初始申请/沟通按钮")
            return {"ok": False, "status": "button_not_found"}
        check = await _verify_sent(context, item, page)
        if check["verified"]:
            note = f"已点击“{clicked}”，并由9227消息侧验证成功：" + "；".join(check["signals"])
            mark_executed(item["job_id"], note)
            return {"ok": True, "status": "verified_sent", "clicked": clicked, **check}
        mark_verification_pending(item["job_id"], f"已点击“{clicked}”，但尚未在9227消息侧确认；禁止再次点击，只允许重新验证。")
        return {"ok": False, "status": "verification_pending", "clicked": clicked, **check}
    except Exception as e:
        if 'clicked' in locals() and clicked:
            mark_verification_pending(item["job_id"], f"点击后验证异常：{e}")
        else:
            mark_error(item["job_id"], str(e))
        return {"ok": False, "status": "error", "error": str(e)[:1000]}
    finally:
        await page.close()


async def apply_job(job_id: str) -> dict:
    p = browser = None
    try:
        p, browser, context = await _context()
        return await execute_one(context, job_id)
    except Exception as e:
        mark_error(job_id, f"9227浏览器未就绪：{e}")
        return {"ok": False, "status": "browser_unavailable", "error": str(e)[:1000]}
    finally:
        if p is not None:
            try: await p.stop()
            except Exception: pass


async def loop_forever() -> None:
    p = browser = None
    try:
        p, browser, context = await _context()
        while True:
            for item in pending():
                await execute_one(context, item["job_id"])
            await asyncio.sleep(POLL)
    finally:
        if p is not None:
            try: await p.stop()
            except Exception: pass


def main() -> None:
    ap = argparse.ArgumentParser(description="Job Agent BOSS 9227 executor")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--open-job", help="只在9227打开岗位，不点击任何申请按钮")
    g.add_argument("--apply-job", help="执行已经由用户明确确认的一次初始申请/沟通动作")
    g.add_argument("--verify-job", help="不再点击，只到9227消息侧重新验证之前那次动作是否已发送")
    args = ap.parse_args()
    if args.open_job:
        print(asyncio.run(open_job(args.open_job)))
    elif args.apply_job:
        print(asyncio.run(apply_job(args.apply_job)))
    elif args.verify_job:
        async def _run_verify():
            p = browser = None
            try:
                p, browser, context = await _context()
                return await verify_job(context, args.verify_job)
            finally:
                if p is not None:
                    try: await p.stop()
                    except Exception: pass
        print(asyncio.run(_run_verify()))
    else:
        asyncio.run(loop_forever())


if __name__ == "__main__":
    main()
