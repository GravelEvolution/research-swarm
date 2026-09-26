# 科研蜂群 · 对话式研究空间

双击 **start-research.cmd**，打开 **http://127.0.0.1:4381**。点击「配置 DeepSeek」，填一条 API Key 并保存测试，即可开始。接口地址、模型和任务调度已预设，不需要再配置检索服务密钥。

本版按照最新三张草图整体重做前端与交互：

1. 左边只有科研任务列表和「新建科研任务」。新任务右侧从一个输入框开始。
2. 用自然语言描述问题，AI 生成 Markdown 需求文档。可在线编辑；结束一段编辑后自动保存并润色，引导明确对象、验收与限制。版本冲突保留用户原稿。
3. 点击「开始研究」后自动检索、构建结构、逐级分解与研究。工作区以真实 3D 节点图为主，可旋转、缩放、拖动；悬浮看节点当前动作，点击查看细节并从该节点深入。
4. 对话区域显示总 agent 的动作、可审查依据与输出。AI 根据材料缺口追加研究和补充论文，不要求用户逐篇读论文或筛选推荐。
5. 完成后查看结果、产物和过程，下载研究档案；继续发送消息即可准备下一轮，既有结果保留。

界面使用 Ant Design。新增内容、生成文字和状态变化采用参考 [Text reveal](https://neoui.el107t.cn/demo/index.html#textReveal) 的模糊入场；内容不变时不会因轮询反复播放。长段落按块过渡，保留中文、emoji、代码换行与复制操作，并尊重系统的减少动态效果设置。

研究期间可暂停或修改方向。论文、页码证据、节点输入输出和执行历史是可选溯源详情。AI 输出保持候选判断，界面不会伪造用户认可或科学验证。

## 安装与数据

本交付包含构建好的界面，正常使用无需 Node.js。需要 Python 3.10+；启动脚本会安装缺失的 PDF 文本读取依赖。当前电脑已具备 Python 与依赖。开发前端才需要 Node.js 20.19+ / 22.12+。

默认接入原目录 `C:\Users\dcm_0\XiaomiMiMoProjects\.mimo-sessions\2026\09\26\ai-access`，将已有课题作为独立科研任务迁入。其 51 篇论文、33 个原切面节点、9 份 PDF 会复制到本任务目录，原库不修改。原目录不存在时使用随包提供的 `vendor/ai-access` 无密钥检索运行模板，从空任务开始。

每个任务的对话、Markdown 版本、论文 SQLite、PDF、研究节点和产物都独立保存在 `.research-state/tasks/<任务ID>/`。全局 Key 保存在本机 `.research-state/config.local.json`，不由 API 回传，不进入报告导出；请勿分享这个状态目录。

默认 DeepSeek 地址 `https://api.deepseek.com`，模型 `deepseek-flash`，与 [DeepSeek 官方文档](https://api-docs.deepseek.com/) 核对于 2026-09-26。也支持环境变量 `DEEPSEEK_API_KEY`。未配置 Key 时可先保存本地需求草稿和核验已有材料，界面会标明尚未启用模型。

## 研究与材料

公开检索使用 OpenAlex，必要时回退到 Crossref / arXiv。新任务使用当前课题的检索词和节点结构，不继承原项目固定的多模态/IEEE 检索范围；具体行为见 `docs/sources-isolation.md`。

模型可调用资料检索、论文读取、证据定位、切面读取等专用工具。已知来源提供公开 PDF 时，会尝试下载到本任务后逐页提取，保留文件指纹和页码；不支持的来源、网络失败、扫描页、缺全文、缺实验数据会明确报告。原库已有的 51 条证据是摘要，PDF 文件存在不等于已经全部阅读或复现。

DeepSeek 结构化调用启用 [官方 JSON 输出模式](https://api-docs.deepseek.com/zh-cn/guides/json_mode/)，格式错误和结构/引用错误各有一次有限纠正，持续失败保留日志供重试。节点到达分解深度上限会执行具体任务并回传，不继续递归拆解。

中央节点可以在汇总后追加具体研究子任务。默认最多 3 个工作节点并行、80 个任务、4 层需求分解、每轮最多 3 次研究迭代，每个获准的检索分支共享 2 次补充检索预算。边界用于控制资源与避免循环，不是用户必须走的固定科研流程。

已有实验工具可对用户提供的真实数值执行分组统计与 bootstrap 差值区间，生成带 SHA-256 的 JSON 凭据。模型能提出新方法与实验设计；论文代码复现、GPU 训练和真实设备测量仍需要相应数据与执行环境，未完成的工作不会写成实验结果。

完成后可导出 ZIP：报告、需求 Markdown、对话、需求版本、研究 JSON、证据、活动、历次节点输入输出以及已生成实验数据。无需额外最终确认弹窗。

## 开发

```powershell
python -m pip install -r requirements.txt
npm.cmd ci
npm.cmd run build
python -X utf8 -m unittest discover -s tests -v
node --test frontend/tests/*.test.mjs
python -X utf8 -m research_swarm --port 4381
```

隔离运行可加 `--state-dir "其他状态目录"`，更换资料源可加 `--source "其他ai-access目录"`。服务只监听 127.0.0.1，前端与 API 同源。React / [Ant Design](https://ant.design/) / [Three.js 3D Force Graph](https://github.com/vasturiano/3d-force-graph)；Python 专用科研调度器。没有通用 DSH agent，研究策略与工具执行职责分开。

源码仓库包含 `dist/` 预构建界面。修改前端后运行 `npm.cmd run build`，将源码和对应构建结果一并提交。运行数据、API Key、论文、数据库与依赖目录均由 `.gitignore` 排除。

最新交互约定见 `docs/conversation-contract.md`，验收记录见 `docs/acceptance.md`。旧六页设计文档保留为历史，不代表当前用户流程。
