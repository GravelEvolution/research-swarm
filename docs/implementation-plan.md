# 科研蜂群实施计划

> 初始规格实施记录，已由用户的新交互要求替代。当前已实现：独立科研会话、单 Key 设置、可编辑 Markdown 与版本保护、按课题隔离检索、Three.js 3D 结构、自主逐级研究/汇总、节点深入与证据追踪、结果与档案导出。实时模型联调、前端与后端验证结果见 acceptance.md。下列六页/四检查点是原版规划，不代表待实施事项或当前 UI。

> For agentic workers: use superpowers:subagent-driven-development or superpowers:executing-plans. Independent frontend, engine, and library components follow the shared API contract and may be implemented in parallel.

Goal: 交付真实连接已有论文库、可观测且由用户控制科研决策的本地应用。
Architecture: Python 标准库 HTTP/SQLite 调度层，React/Semi Design 前端，受限科研执行器与 ai-access 适配器。
Spec: design.md。共享数据与函数约定见 contract.md。

## Global constraints

- Windows 本地运行，用户数据与模型密钥只在本机后端；原论文 ID/证据 ID 稳定保留。
- 四个人工检查点不可自动确认；AI 只生成候选。
- 六页面，标准 Semi UI，操作在抽屉/弹窗完成。
- 真实运行、数据核验与测试模拟有明确区别。

## Review focus

并发确认/修改、回滚后迟到结果、断网与模型失败、无证据与摘要证据、空库/新课题，都必须有明确可恢复行为。

## Tasks

- [ ] 数据适配：library.py；读取现有库的论文、树、关联、评分、证据，拒绝未知定位。tests/test_library.py 使用临时库并核对真实只读导入。
- [ ] 调度与持久化：engine.py/store.py；先测试四个检查点、子任务聚合、干预影响、回滚、版本和重启，再实现。python -m unittest discover -s tests。
- [ ] Semi UI：frontend/；依照 contract.md 实现六页面、全局状态、抽屉、检查点和干预预览；npm run build 验证。
- [ ] 工具和模型：runner.py/providers.py；真实资料核验、严格 JSON 模型输出、证据校验、清晰失败、受限实验工具；先写风险边界测试。
- [ ] HTTP 和交付：server.py、启动脚本、README；本机 API、SSE、导出、设置。浏览器验证实际操作，记录未验证能力，打包源码与运行说明。
