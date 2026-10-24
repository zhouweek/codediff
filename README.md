# CodeLens · 代码相似性分析平台

面向毕业实习作品查重：浏览器登录后，一次选择同一 HTTPS Git 仓库的 2–100 个学生分支，可扣除教师模板，查看全班相似度矩阵、优先复核名单和原始行号证据。双版本分析继续可用。

当前需求：[HTML PRD v2](prd/prd.html) · [Markdown PRD v2](PRD-v2.md)。批量接口与缓存设计见 [批量技术说明](TECHNICAL_DESIGN-v2.md)，双版本设计见 [v1 技术说明](TECHNICAL_DESIGN.md)。[v1 PRD](PRD.md) 保留作为历史资料。[50 分支批次报告](reports/batch-50-report.html) 和 [双版本报告](reports/sample-report.html) 均来自模拟源码的真实引擎计算。

50 分支示例包含 49 个有独立代码的作品、1 个仅含模板的作品；全部 1,225 个作品对都有状态，其中 1,176 对完成比较、49 对为 N/A。[公网 Git 三分支实测报告](reports/git-batch-report.html) 使用 Express 的 `4.x`、`5.x`、`master`，验证批量获取路径；这些分支共享历史，不表示抄袭。

[下载 50 分支带证据报告包](reports/batch-50-report-package.zip)，完整解压后打开其中的 `index.html`。矩阵行轴含作品标签和颜色图例，点击数值或名单即可查看两种口径证据。证据页展示模板扣除明细、自动换行和双栏同步滚动。

[GitHub 实测报告](reports/git-branch-report.html) 比较 Express 的 `4.x` 与 `5.x` 分支，排除 `test/**` 与 `examples/**`：确认 19 个文件对、88 个片段，整体覆盖 66.1%。两个分支共享历史，此报告展示检测与证据能力，不表示抄袭。实际提交 SHA 和分析口径已保存在报告中。

## 启动

需要 Python 3.9+、Git 2.30+ 和 Pygments。前端不需要 npm 或构建。

```bash
cd /Users/bytedance/work/my/gitdiff
python3 -m pip install -r requirements.txt
python3 server.py
```

打开 **http://127.0.0.1:8765**，点击“创建账号”，输入一个仓库并加载学生分支，也可手工每行填写一个分支，用 Tab 添加作品标签。教师模板应为独立分支。提交后在矩阵或名单中打开作品对查看证据。没有仓库时，点击“体验 50 分支示例”。

如果系统 Python 不允许安装包，可创建独立环境：

```bash
python3 -m venv ~/.venvs/codelens
~/.venvs/codelens/bin/python -m pip install -r requirements.txt
~/.venvs/codelens/bin/python server.py
```

也可以运行 `bash run.sh`，它会检查已经安装的依赖并启动服务。端口被占用时使用 `python3 server.py --port 8766`，然后打开对应端口。

数据库默认保存到 macOS 的 `~/Library/Application Support/CodeLens/codelens.sqlite3`。使用 `--data-dir 路径` 或 `CODELENS_DATA_DIR` 可指定其他位置。账号、任务、匹配源码和报告记录在此目录中保留；临时 Git 快照会在任务结束时清理。代码库无需初始化为 Git 仓库即可运行。

## 能力与结果阅读

- 支持 Java、JavaScript/JSX、TypeScript/TSX、HTML、C、C++、C#、PHP、Python、Go、Rust、Kotlin、Swift、Ruby、CSS、SQL。
- 支持不同仓库、同仓库不同分支；留空分支时解析远端默认分支。每个结果保存实际提交 SHA。
- 自动忽略注释和空白，可选归一化标识符、字符串与数字。保留语言关键字、运算符以及预处理指令。
- 能检出局部连续片段、变量/函数改名、函数/片段整体换序、跨文件迁移；点击文件对可查看原文与行号。
- A→B 表示 A 中与 B 匹配的代码比例，B→A 对称计算；整体是按有效 token 数加权的覆盖。覆盖区间去重，同一代码位置不重复计数。
- 账号间任务隔离，刷新后可继续查看历史；HTML 导出包含全部确认片段、设置、来源与跳过说明，不需要 CDN 和登录。

仅比较同语言文件。HTML 按模板整体分析；`.h` 按 C 识别，C++ 头文件建议使用 `.hpp`。扩展名识别不会自动检测无扩展名脚本。非 UTF-8、符号链接、子模块、LFS 指针、不支持类型、依赖和构建文件均跳过并说明。

当前仍是词法检测引擎。任意语句重排、数据流变化、跨语言算法等价、SSH 仓库、企业内网 Git、公共来源鉴定和组织权限属于后续能力。教师模板按确认匹配区间扣除，不能保证排除全部公共代码；**相似覆盖比例不等于抄袭概率**。

## 私有仓库

展开来源卡片中的“私有仓库访问凭据”，填写访问用户名和只读令牌。GitHub 用户名可留空；GitLab 可填 `oauth2`，其他服务商遵循其 HTTPS 凭据规则。令牌不会进入数据库、命令行或报告，仅用于当次 Git 请求，失败重试需重新填写。

仓库地址仅接受不含内嵌凭据的公网 HTTPS URL，禁止重定向、本机与内网地址。后台只读取 Git 快照，不执行仓库源码、构建、安装脚本或子模块。私有仓库集成路径已实现，仍需使用相应服务商的实际授权凭据验证。

## 处理范围

批次最多 100 个学生分支；50 分支产生 1,225 个无序作品对。一个批次复用单个 Git 对象库、每次获取最多 8 个分支；同提交快照、相同文件内容与相同归一化作品对复用读取或计算。仅在当前批次内缓存，不跨账号共享。批次 Git 对象累计最多 1 GB，唯一文件有效 token 最多 4,000,000，单作品最多 200,000；持久化缓存序列化前累计最多 128 MB。批次执行串行以控制内存。

矩阵的 N/A 表示失败或无独立代码，不等于 0%。复核默认按任一侧覆盖 ≥70% 且存在匹配片段筛选。单学生分支失败保留其他作品结果，批次显示“部分完成”。总览 HTML 包含全部摘要、双口径矩阵、版本及跳过原因；指定作品对另行导出代码证据。固定快照缓存保存于数据库，令牌不保存。

点击“导出带证据报告包”可取得全部已完成作品对的原始和扣模板后 HTML；ZIP 内的相对链接离线有效，N/A 不生成假证据。导出使用固定快照，失败不返回残缺 ZIP。默认同一时刻生成一个报告包，未压缩内容上限 256 MB、压缩上限 128 MB，分步检查 120 秒生成预算。证据阅读控件默认开启自动换行与双栏同步滚动，可按需关闭；原始行号保持不变。

默认两个分析线程，最多 8 个未完成任务。每次 Git 命令最长 120 秒；每侧 Git 对象最多 200 MB、快照展开最多 100 MB、快照最多 50,000 个文件；每侧可分析文件最多 1,000 个，单文件 512 KB，两侧合计 400,000 有效 token。

候选文件对最多 20,000，确认片段最多 5,000，完整证据 JSON 最多 16 MB。超出硬限制会失败并提示缩小范围，不生成静默截断报告。出现超过 64 次的重复指纹或跨超过 60 个文件的常见指纹可能被忽略，报告会警告相关漏检风险。

本地默认只监听回环地址。面向公网或多人生产部署时，需另行增加 HTTPS、组织身份、网络出口隔离、请求/资源限额和运维。应用的 DNS 预检不能替代基础设施网络隔离。该版本不应被描述为已经完成公网生产部署。

## 验证

运行核心和 HTTP 检查，无额外测试库：

```bash
python3 -m unittest discover -s tests -v
```

检查覆盖七种主要语言、改名与换序、覆盖去重、最小片段边界、字面量选项、预处理指令保留、路径排除、无匹配/空态、源码转义、账号隔离、CSRF、登录退出和中断恢复。

浏览器检查脚本为 `tools/browser_smoke.mjs`，使用 Playwright 和本机 Chrome。可将 Playwright 安装到独立临时目录：

```bash
npm install --prefix /tmp/codelens-browser-check playwright
CODELENS_PLAYWRIGHT_MODULE=/tmp/codelens-browser-check/node_modules/playwright/index.js node tools/browser_smoke.mjs
```

脚本会创建测试账号、运行示例与 GitHub 小仓库分析，验证筛选、证据展开、HTML 下载/离线打开、历史、移动布局与登录退出，并更新示例报告。截图写入系统临时目录。不要在重要业务账号上运行；测试账号独立且不会发送任何消息。

## 源码结构

```text
server.py                 账号、会话、任务队列、持久化与 API
codelens/engine.py         多语言 token、winnowing、片段核对、覆盖计算
codelens/git_source.py     HTTPS Git 分支快照与资源边界
codelens/report.py         转义、离线 HTML 与证据排版
codelens/demo.py           多语言固定示例
web/                      浏览器界面，无构建依赖
tests/                    检测与 HTTP 行为检查
tools/                    PRD 生成与可选浏览器验证
prd/                      可阅读 PRD 与本地字体/脚本
reports/                  可离线阅读的示例报告
```

修改 `PRD-v2.md` 后运行 `python3 tools/build_prd.py` 即可重建 HTML。批量浏览器验证可运行 `CODELENS_PLAYWRIGHT_MODULE=/tmp/codelens-browser-check/node_modules/playwright/index.js node tools/browser_batch.mjs`。PDF 可在 HTML 报告中通过浏览器打印保存。
