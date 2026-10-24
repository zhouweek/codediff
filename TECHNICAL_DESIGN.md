# CodeLens 首版技术说明

本文记录 v1 双版本模式。当前毕业作品批量模式、模板扣除与固定快照缓存见 [TECHNICAL_DESIGN-v2.md](TECHNICAL_DESIGN-v2.md)。

## 运行与组成

Python 3.9+、Git 2.30+、Pygments 2.19+。浏览器前端使用原生 HTML/CSS/JavaScript，无前端构建和 CDN。后端使用 Python HTTP 服务、SQLite、后台任务执行器。本地默认地址为 `http://127.0.0.1:8765`。

`server.py` 提供账号、会话、分析任务和导出接口；`codelens/engine.py` 进行词法分析与片段匹配；`codelens/git_source.py` 获取固定提交快照；`codelens/report.py` 生成离线 HTML；`web/` 提供浏览器工作台。`PRD.md` 为规范来源，`prd/prd.html` 为可阅读版本。

数据库和临时仓库放在系统应用数据目录，避免进入项目源码；`CODELENS_DATA_DIR` 可覆盖数据库位置。临时仓库在 `TemporaryDirectory` 内，任务结束即删除。两线程任务执行器，最多保留 8 个未完成任务。

## 检测口径

Pygments 按语言词法规则产生 token，丢弃注释和空白。标识符默认归一化，关键字、运算符、HTML 标签/属性与预处理指令保留；字面量归一化可选。输出保留 token 到原始文件字符位置和行号的映射。

每个 k-token 片段生成 64 位滚动指纹；采用右侧最小值的 winnowing 窗口。窗口宽度为 5，k=最小片段长度−4，因此达到配置长度的连续相同片段拥有公共指纹。指纹发现候选后，逐个核对归一化 token 并双向扩展到连续片段边界。指纹哈希本身不构成证据。

片段以来源位置去重，按匹配长度排序；各文件的覆盖区间并集用于去重计数。文件对覆盖率与仓库覆盖率分别计算，所有覆盖指标均基于参与分析的 token。

跨文件候选按同语言指纹倒排索引生成。为限制资源，超过 64 次出现的重复指纹、出现在超过 60 个文件的常见指纹可被忽略，并在报告中警告可能漏检。候选超过 20,000、有效证据总数超过 5,000 或完整证据 JSON 超过 16 MB 时任务失败，不产生冒充完整结果的截断报告。每个文件对最多处理 30,000 锚点，超限失败。指纹筛选、最小片段阈值和词法归一化均可能造成漏检或误报。

相同片段先区分原文、格式/注释、标识符/字面量变化；多个片段的相对位置逆序标记为片段整体换序。该能力不覆盖任意语句重排、数据流语义和跨语言等价性。

## Git 获取与边界

仅支持公网上的 HTTPS URL。校验 URL 无内嵌凭据，解析 DNS 后拒绝任何非公网 IP。取代码禁止 HTTP 重定向、子模块、其他 Git 协议、仓库钩子与自动维护。后台下载不会执行源码。

下载使用 `git init --bare` + `git fetch --depth=1`，并通过 `git archive` 读取快照，不检出符号链接、不给仓库脚本执行机会。读取阶段限制超时与 Git 对象容量；HTTP 响应、文件和 token 同时设定上限。Git 访问凭据通过进程私有环境和临时 askpass 文件传入，命令行/数据库/报告不含令牌。默认分支先由 `ls-remote --symref` 固定名称，报告保存解析后的 SHA。

公网部署需独立增加网络出口允许名单/隔离。应用层 DNS 预检无法完全替代基础设施对 DNS 重绑定、代理路由的约束。首版本地运行仍需选择可信 Git 服务商。

## API 与权限

- `GET /api/session`：返回当前账号与 CSRF token；未登录返回 401。
- `POST /api/register`、`POST /api/login`、`POST /api/logout`：账号与会话。
- `GET /api/jobs`：本人历史与状态。
- `POST /api/jobs`：提交 `{name, a:{url, branch, username, token}, b:{...}, options:{normalize_identifiers, normalize_literals, min_match, excludes}}`。
- `POST /api/demo`：用固定多语言示例运行同一引擎。
- `GET /api/jobs/{id}`：本人任务；执行中返回阶段，完成后包含分析结果。
- `GET /api/jobs/{id}/report`：下载离线 HTML。

所有任务按 owner_id 查询；不存在或不属于本人均返回 404。Cookie 为 HttpOnly、SameSite=Strict；写请求校验同源 Origin，已登录写操作还要求 `X-CSRF-Token`。密码使用 PBKDF2-HMAC-SHA256 加盐摘要；会话随机生成，数据库仅保存 token 摘要，7 天过期。静态文件路径按精确白名单提供，PRD 只读目录单独限制路径。

## 验证与部署范围

验证词法归一化、函数换序、无关文件、跨文件迁移、覆盖去重、最小匹配长度、非 UTF-8、HTML 源码转义、账号隔离、CSRF、Git 分支解析、报告导出和服务重启恢复。浏览器完成注册登录、真实引擎演示、证据展开、过滤和退出。

这是具备真实后端和真实检测引擎的本地首版。生产 HTTPS、组织 RBAC、网络隔离、水平扩展、密码恢复、公共代码基线、AST/语义引擎尚未实现。
