# agentjob — 本地 AI 求职 Agent

`agentjob` 是一个本地优先、面向 BOSS 直聘登录态浏览器的 AI 求职工作台。它不是“自动海投脚本”，而是一条完整的岗位处理流水线：

**发现岗位 → 获取完整 JD → 硬规则筛选 → AI 适配评分 → 公司/岗位背调 → 沟通与简历建议 → 用户确认 → 9227 浏览器执行一次投递/沟通 → BOSS 消息侧验证 → 防重复。**

前端默认使用白天模式，也保留黑夜模式；用户可以直接在 Dashboard 里上传简历 PDF、维护求职要求和补充资料，不需要手改 JSON。

> 风险提示：测试阶段强烈建议使用你有权使用的独立测试账号/非主账号，降低主账号因平台规则、登录异常或自动化行为受到影响的风险。不要使用额外账号规避封禁、安全验证或平台账号规则；验证码与安全校验必须人工处理，项目不会绕过。

> 运行前置条件：使用本项目时必须已经有一个可用 Agent，并且该 Agent 已接入可以正常调用的模型。项目不限定具体 Agent、模型或供应商；如果 Agent/模型未接入，PDF 语义整理、L2 AI 适配、L3 背调和 L4 材料生成只能保持 pending/needs_ai，不能把规则结果冒充 AI 结果。

---

## 1. 这个项目已经提供什么

### 1.1 BOSS 岗位获取

主数据源是 **BOSS 登录态完整岗位页**，不是只依赖公开搜索摘要。

项目通过专用浏览器端口 `9227` 工作：

```text
Chrome / Edge
  --remote-debugging-port=9227
        ↓
Playwright connect_over_cdp
        ↓
BOSS 登录态搜索页 / job_detail
        ↓
完整 JD / 公司信息 / 招聘人 / 地址 / 标签 / 福利等
```

不需要安装浏览器插件、油猴脚本或扩展。

默认优先 Chrome：

```text
C:\Program Files\Google\Chrome\Application\chrome.exe
```

如果没有 Chrome，启动器会尝试 Edge：

```text
C:\Program Files\Microsoft\Edge\Application\msedge.exe
C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe
```

浏览器 profile 保存在本地，不应提交到 GitHub。

### 1.2 L0–L6 岗位流水线

```text
L0 发现并补齐完整 JD
 ↓
L1 硬规则筛选
 ↓
L2 AI 岗位适配
 ↓
L3 公司 / 岗位背调
 ↓
L4 沟通与简历材料
 ↓
L5 用户确认 + 一次初始投递/沟通
 ↓
L6 持续聊天默认不自动发送
```

核心原则：

- 安全页不是 reject。
- JD 不完整时不能进入正式 L2。
- Agent 或模型未接入时不能把规则分冒充 AI 分。
- L2 未通过时不能越级进入 L4。
- 投递必须由用户明确点击确认。
- 同一 JD 版本验证成功后禁止重复投递。

### 1.3 评分与每日推荐

每日推荐是 **最多 7 条**，不是必须凑满 7 条。

有 1 条合格就显示 `1/7`，有 3 条就显示 `3/7`。

默认 final score：

```text
55% L2 岗位匹配
25% 公司质量
20% 机会质量
```

机会质量可综合：

- 薪资
- 新鲜度
- 工作制度
- 招聘人活跃度
- 目标岗位优先级
- 成长/转正等机会信号

最终 Top 区仍要服从硬条件；旧 L2/L3 高分不能覆盖当前 L1 reject。

### 1.4 公司 / 岗位背调

L3 负责真正的 due diligence，而不是把搜索结果拼起来。

应调查：

- 公司主体、成立时间、规模
- 主营业务与产品
- 融资与投资人
- 经营 / 法律风险
- 福利与工作文化
- 加班、弹性、远程、下午茶、餐补等
- 网络公开讨论
- 裁员 / 业务风险
- 是否长期重复招聘
- 是否外包 / 派遣 / 长期驻场
- 当前岗位薪资竞争力
- 当前 JD 是否像真实 HC

输出分两层：

**正文层**：给用户看的正常中文结论。

**证据层**：source matrix，保留来源、日期、置信度与冲突。

匿名讨论只能作为 signal，不能当作公司事实。

### 1.5 前端工作台

Dashboard 默认地址：

```text
http://127.0.0.1:8799
```

当前主要能力：

- 白天 / 黑夜模式
- 每日推荐 Top 7
- 其他值得看岗位
- 收藏 ★ 置顶
- 搜索岗位 / 公司 / 技能
- 完整 BOSS JD
- L2 推荐理由与风险
- L3 公司背调
- 网络评价
- 证据矩阵
- 沟通建议
- 开场话术
- 简历重点
- 仅打开 BOSS
- 投递
- 重新验证发送
- 暂缓
- 跳过

### 1.6 简历与用户资料

Dashboard 右上角提供：

```text
上传简历 PDF
求职要求
补充资料
```

PDF **只保存源文件**，项目不再做机械 PDF 文本解析，因为复杂版式很容易产生错序/乱码。

上传后：

```text
PDF 原件
 ↓
data/profile/resume/current.pdf
 ↓
生成 agent_request.json
 ↓
等待本地 Agent 语义处理
```

本地 Agent 必须先阅读：

```text
我的资料.txt
补充资料.txt
求职要求.txt
```

再读取 PDF 源文件做校验/补全。

AI 不得把未出现的学历、任职经历、工作年限或成果数字猜成事实。

---

## 2. 第一次使用：Agent 必须做什么

如果你是接手这个仓库的 AI Agent，第一次初始化时不要直接抓岗位。

### Hook A — BOOTSTRAP / FIRST_RUN

触发条件：

- 第一次 clone / 第一次运行；或
- 本地配置不存在。

Agent 应：

1. 阅读本 README。
2. 运行 bootstrap / doctor。
3. 检查 Python 与 Playwright 依赖。
4. 检查 `8799` Dashboard。
5. 检查 `9227` 浏览器。
6. 若 9227 未运行，启动专用 Chrome；Chrome 不存在再使用 Edge。
7. 打开 BOSS 首页。
8. 明确提醒用户：**请在这个 9227 浏览器里登录 BOSS。测试阶段强烈建议使用你有权使用的独立测试账号/非主账号。**
9. 不要求用户把 Cookie、Token 手工复制给 Agent。
10. 不绕过验证码 / 安全验证。
11. 提醒用户打开 Dashboard：

```text
http://127.0.0.1:8799
```

12. 提醒用户完成三件事：

```text
① 上传简历 PDF
② 填写求职要求
③ 填写补充资料
```

13. 用户完成后，再读取三份资料和 PDF 请求状态。
14. 只有资料准备完成后，才进入岗位发现。

Agent 第一次向用户提示时，应至少说明：

> 我已经打开专用 9227 浏览器。请在这个浏览器中登录 BOSS；测试阶段建议使用你有权使用的独立测试账号/非主账号。登录完成后，请打开 Job Agent 工作台，在右上角上传简历 PDF，并填写“求职要求”和“补充资料”。这些完成后我再开始采集和筛选岗位。

---

## 3. 每次使用：Agent 的标准生命周期

### Hook B — SESSION_START

每次新的 Agent / 新对话开始工作时：

1. 检查项目状态与数据库。
2. 检查 Dashboard `8799`。
3. 检查 9227。
4. 检查 BOSS 是否仍登录。
5. 检查资料是否变化。
6. 检查是否有 `agent_request.json = pending`。
7. 检查上一次是否有：
   - `verification_pending`
   - 待确认投递
   - 已收藏岗位
   - 已跳过岗位
8. 不自动清空历史状态。

### Hook C — PROFILE_UPDATED

触发：

- 新上传 PDF；
- 修改补充资料；
- 修改求职要求；
- 用户明确说“我更新了个人资料”。

Agent 应：

1. 先读当前个人资料。
2. 再读求职要求。
3. 再读补充资料。
4. 如果 PDF 请求 pending，再读取源 PDF。
5. 整理候选人画像。
6. 只保留可证明事实。
7. 明确标记未知字段。
8. 更新正式候选人资料。
9. 如果用户要求重新筛选，再重跑已有岗位；否则不要静默改变历史结果。

### Hook D — RADAR_RUN

触发：

- 用户明确要求找工作；
- 用户明确开启每日/定时 radar；
- 当前合格候选不足且用户允许继续发现。

Agent 应：

1. 用 9227 登录态打开目标 BOSS 搜索页。
2. 先抓卡片层做低成本预筛。
3. 对值得看的候选打开 `/job_detail/`。
4. 保存完整 JD。
5. 遇到安全验证：停止该来源并提醒用户，不绕过。
6. 不同时开启多个 writer 抢 SQLite。
7. 发现一条合格岗位就允许前端立即显示，不等待凑满 7 条。

### Hook E — JOB_DETAIL_COMPLETE

当一个岗位 L0 完整详情到齐：

1. 计算稳定 `content_hash`。
2. 判断是否是旧 JD 的真实更新。
3. 如果完全相同且历史已 skip / applied，不重新推荐。
4. 如果职责、要求、薪资、工作制度等核心 JD 真的改变，才作为新版本重新评估。
5. 运行 L1。
6. L1 pass 后才进入 L2。

### Hook F — L2_PASS

L2 应读取：

```text
完整 JD
候选人画像
求职要求
L1 结果
```

输出至少包含：

- score
- verdict
- fit_summary
- strengths
- gaps
- risks
- questions
- resume_focus

不要只看岗位标题。

### Hook G — L3_DUE_DILIGENCE

触发：L2 pass 且达到背调阈值。

Agent 应优先查：

```text
A. BOSS 原始公司信息
B. 公司官网 / 招聘官网
C. 工商 / 监管 / 官方披露
D. 主流媒体 / 融资信息
E. 公开讨论与匿名社区（仅 signal）
```

每条重要结论保留：

```text
claim
value
confidence
source_type
published_at
retrieved_at
source
```

L3 完成后给用户的正文应是可读中文，而不是 JSON：

```text
公司结论
业务与产品
融资 / 投资人
经营 / 法律风险
福利
工作文化
招聘模式
岗位竞争力
网络评价
```

同时保留 source matrix 供核验。

### Hook H — L4_MATERIALS

触发：L2 pass + L3 complete。

Agent 输出：

- 为什么值得投
- 需要注意什么
- 与招聘者沟通时先问什么
- 推荐开场话术
- 简历应突出哪些项目
- 哪些事实不能写
- 面试可能被追问什么

不得虚构候选人的教育、公司经历、成果数字或技能熟练度。

### Hook I — APPLY_CLICK

只有用户亲自点击 Dashboard 的“投递”才触发。

Agent / executor 应：

1. 确认 L4 complete。
2. 确认当前 JD 版本从未 `verified_sent`。
3. 自动确保 9227 在线。
4. 在 9227 打开准确岗位页。
5. 只点击一次支持的初始申请/沟通按钮。
6. 不继续无人监督聊天。
7. 去 BOSS 当前页 / 消息页做只读验证。
8. 验证到对应公司 / 岗位会话后标记：

```text
verified_sent
```

9. 此 JD 版本以后不能再次投递。

如果点击后无法确认发送是否成功：

```text
verification_pending
```

之后只能执行“重新验证发送”，**绝不能再次点击投递按钮**。

### Hook J — SKIP / FAVORITE

**收藏 ★**：

- 始终置顶；
- 不等于已经投递；
- JD 更新后收藏状态保留。

**跳过**：

- 当前 JD 版本不再进入推荐；
- 再次扫描到完全相同 JD 也不恢复；
- 只有稳定内容 hash 变化才重新开放评估。

---

## 4. 用户需要做什么

用户不需要理解 SQLite、JSON 或 Playwright。

### 第一次配置

1. 运行 `install.bat` 或安装 requirements。
2. 运行：

```text
run_agent.bat
```

3. 项目会启动 Dashboard 和专用 9227 浏览器。
4. 在 **9227 这个专用浏览器**里登录 BOSS。
5. 测试阶段强烈建议使用你有权使用的独立测试账号/非主账号，避免主账号承受测试风险。
6. 如果出现验证码或安全页，由用户本人完成。
7. 打开：

```text
http://127.0.0.1:8799
```

8. 右上角：

```text
上传简历 PDF
求职要求
补充资料
```

9. 完成后告诉 Agent：“资料已完成，可以开始找岗位”。

### 日常使用

用户主要只需要：

- 查看推荐岗位
- 收藏 ★
- 修改求职要求
- 更新补充资料
- 上传新简历
- 暂缓 / 跳过
- 点击“仅打开 BOSS”人工查看
- 点击“投递”做一次明确确认
- 若显示 `verification_pending`，点击“重新验证发送”

不需要重复投同一个 JD。

---

## 5. 浏览器与 9227

### 为什么使用 9227

正常浏览器已经拥有用户登录态，Playwright 通过 CDP 接入它，因此不需要导出 Cookie。

```text
用户真实浏览器会话
        ↓
remote debugging :9227
        ↓
Playwright CDP
        ↓
读取 / 操作 BOSS 页面
```

### 不需要什么

- 不需要浏览器插件
- 不需要手工复制 Cookie
- 不需要抓取私有 API
- 不需要 stealth 插件
- 不需要验证码绕过

### Chrome / Edge

优先 Chrome。

如果 Chrome 不存在，自动回退 Edge。

如果两者都没有，Agent 应停止并告诉用户安装 Chrome 或 Edge，而不是换成未知浏览器继续执行。

---

## 6. 数据与本地隐私

本地数据库：

```text
data/job_agent.sqlite3
```

重要表：

```text
jobs
stage_runs
company_reports
company_sources
materials
application_gates
application_history
job_ui_state
```

公开仓库不得包含：

- 用户真实 PDF
- `我的资料.txt`
- `求职要求.txt`
- `补充资料.txt`
- SQLite
- `data/profile/`
- 浏览器 profile
- Cookie
- Token
- API key
- executor logs
- 用户真实 L3 历史资料

公开仓库使用 `examples/` 模板和 `.gitignore` 隔离个人数据。

---

## 7. 安全与平台边界

本项目的目标是本地辅助求职，不是规避平台控制。

禁止：

- CAPTCHA 绕过
- 安全页绕过
- 指纹伪装 / stealth 逃避检测
- 逆向未公开 BOSS 私有 API
- 用多个账号规避平台封禁
- 无人监督持续骚扰招聘者
- 已验证发送后重复投递同一个 JD

允许：

- 使用用户本人已登录浏览器
- 正常打开搜索页 / 详情页
- 在用户明确确认后点击一次申请/沟通
- 读取消息页面验证是否发送成功
- 生成沟通建议供用户使用

---

## 8. 启动命令

公开仓库第一次 clone：

```bash
python bootstrap.py
pip install -r requirements.txt
python job_agent.py doctor
```

Windows 推荐：

```text
install.bat
run_agent.bat
```

单独检查：

```bash
python tools/check_9227.py
python tools/start_9227.py
python tools/check_dashboard.py
```

Dashboard：

```text
http://127.0.0.1:8799
```

CDP：

```text
http://127.0.0.1:9227
```

---

## 9. Agent 接手本项目后的第一句话

如果一个 AI Agent 第一次读取本 README，建议主动告诉用户：

> 我已经理解 agentjob 的工作流。第一次使用时，我会先检查 Dashboard 和 9227 专用浏览器，而不是直接开始抓岗位。如果 9227 尚未启动，我会优先启动 Chrome，没有 Chrome 再使用 Edge。请你在这个专用浏览器中登录 BOSS；测试阶段建议使用你有权使用的独立测试账号/非主账号，并自行完成任何验证码或安全校验。随后请在 `http://127.0.0.1:8799` 右上角上传简历 PDF、填写求职要求和补充资料。完成后我会先整理个人画像，再开始发现岗位、评分和背调。任何真实投递都必须由你主动点击确认。

---

## 10. 当前产品哲学

agentjob 不追求“自动投得越多越好”。

它更接近：

```text
一个会持续帮你看岗位、做研究、记住投递状态、减少重复劳动的本地求职 Agent。
```

正确行为是：

- 不足 7 条就少于 7 条；
- 不够确定就标待确认；
- 没有证据就不编；
- 已经投过就不重复；
- 用户资料改变后重新理解，而不是拿旧画像硬算；
- 用户永远保留最终投递决定权。

项目主页：

https://github.com/hamliy-feng/agentjob
