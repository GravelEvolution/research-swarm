# 对话式科研工作区 v2

替代原六页界面和固定四关交互。用户最新草图为准。左侧仅新建科研任务与任务列表，右侧围绕单一输入框；准备时居中，研究时3D结构图占主要面积且下方显示总agent动作/可审查依据/输出，完成时显示报告和产物。新一轮消息返回需求准备。论文阅读和溯源都是可选详情。

## API

- GET `/api/tasks` → `{tasks:TaskSummary[]}`；POST `/api/tasks` `{}` → TaskDetail（独立空任务）。
- GET `/api/tasks/:id` → TaskDetail；轮询1.5秒，后台运行持久化，不伪造进度。
- POST `/api/tasks/:id/messages` `{text:string}` → TaskDetail。异步生成或更新MD；完成后发起下一轮，运行时先暂停，保留历史结果。用户消息与agent回应持久化。
- POST `/api/tasks/:id/document` `{markdown:string,expectedRevision:number}` → TaskDetail。保存用户版本后异步AI润色，编辑竞争校验；保留原始版本及修改理由。用户结束一段编辑自动保存/润色，不能覆盖未提交键入。
- POST `/api/tasks/:id/start` `{expectedRevision:number}` → TaskDetail。用户显式开始，检索、建图、自主研究自动运行，无推荐筛选/对比/最终确认弹窗。
- POST `/api/tasks/:id/actions/{pause,resume,retry,impact,intervene}`：沿用engine动作。POST `/api/tasks/:id/deepen` 同原deepen。返回TaskDetail（impact只返回影响对象）。
- GET `/api/tasks/:id/nodes/:nodeId/history` → `{executions:[...]}`（旧API结构在实现核对）。GET `/api/tasks/:id/papers/:paperId/pdf`；GET `/api/tasks/:id/export` → ZIP（完成即可导出，候选不伪造为人工认可）。
- GET/POST `/api/settings` 全局模型设置；POST `/api/provider/test` 测试。配置窗口保留为边栏底部小按钮。

TaskSummary `{id,title,phase,updatedAt,round}`。
TaskDetail `{task:TaskSummary,phase:'empty'|'requirements'|'retrieving'|'researching'|'completed'|'failed',document:{markdown,revision,polishing,polishedFrom:number|null,source:'model'|'local',questions:string[],error:string|null},messages:Message[],state:Snapshot|null,error:string|null,artifacts:{name,kind,url}[],runs:RunSummary[],modelReady:boolean}`。polishedFrom标识AI结果基于哪一个用户版本，前端不能只凭source推断是不是自己的润色。
Message `{id,role:'user'|'assistant'|'system',content,at,kind?:'requirements'|'progress'|'result'}`。RunSummary `{round,at,summary,mode}`。
Snapshot同contract.md，不展示固定9阶段和四检查点。`report.ready=true`为生成完成，`approved`仍false；不能将模型完成等同用户确认。研究内的plan/execute/aggregate仅是节点调度阶段，不是用户向导步骤。

## 3D结构图

真实WebGL/Three.js三维渲染，可旋转/缩放/拖动节点。节点来自state.nodes，连线来自decompose/return/compare和切面父子关系。可先显示检索形成的完整结构，再动态加入AI子任务；不可只有主节点和随机装饰点。鼠标悬浮显示名称、状态、当前动作；点击展开日志/输入/输出/证据、从此节点深入研究。状态颜色简洁，不加光效/粒子/自动旋转。保留可访问节点搜索/列表入口作为补充。

## 专用执行边界

自然语言需求由配置模型产出MD和结构化需求/检索式；未配置模型时明确本地草稿和资料核验，不宣称AI创新。研究开始后模型可在受限预算内继续提出子任务/补充检索，用户只需要阅读结果。每个新任务建立独立原检索程序工作区，不污染原ai-access任务和不同科研任务；已有课题可作为独立迁入任务。
