import { Component, Suspense, useEffect, useRef, useState, type ReactNode } from 'react';
import { Alert, App as AntdApp, Button, Drawer, Input, Modal, Space, Spin, Tooltip } from 'antd';
import { ApartmentOutlined, CommentOutlined, FileTextOutlined, PauseOutlined, PlayCircleOutlined, PlusOutlined, ReloadOutlined, SearchOutlined, SettingOutlined } from '@ant-design/icons';
import { api, messageOf, taskPath, useTaskWorkspace } from './taskApi';
import { useDocumentDraft } from './useDocumentDraft';
import { Markdown } from './Markdown';
import { DeepSeekSettings, TaskNodeDrawer, TaskPaperDrawer } from './TaskOverlays';
import { AgentActivity, Composer, HistoricalReport, ResearchGraph, ResultView, timeLabel } from './ConversationParts';
import { taskPhaseLabels, type TaskDetail, type TaskSummary } from './taskTypes';
import { isModelReady, preparationReply } from './conversationState';
import { BlurChange, BlurText, useLiveMotion } from './BlurReveal';
import type { VisualNode } from './graphData';
import type { Settings } from './types';

function Workspace() {
  useLiveMotion();
  const { message } = AntdApp.useApp();
  const workspace = useTaskWorkspace();
  const { detail } = workspace;
  const editor = useDocumentDraft(detail, workspace.accept);
  const [composer, setComposer] = useState('');
  const messageDrafts = useRef(new Map<string, string>());
  const composerTask = useRef(workspace.selectedId || 'new');
  const composerRef = useRef(composer); composerRef.current = composer;
  const [busy, setBusy] = useState('');
  const [actionError, setActionError] = useState('');
  const [settings, setSettings] = useState<Settings | null>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [preview, setPreview] = useState(false);
  const [resultTab, setResultTab] = useState('report');
  const [selectedNode, setSelectedNode] = useState<VisualNode | null>(null);
  const [selectedPaper, setSelectedPaper] = useState<string | null>(null);
  const [tasksQuery, setTasksQuery] = useState('');
  const [historyOpen, setHistoryOpen] = useState(false);
  const [historic, setHistoric] = useState<{ round: number; data: Record<string, unknown> | null; error: string } | null>(null);
  const [compact, setCompact] = useState(() => window.matchMedia('(max-width:760px)').matches);
  const modelReady = isModelReady(detail?.modelReady, settings?.capabilities.modelReady);
  useEffect(() => {
    let active = true;
    const refreshSettings = () => { void api<Settings>('/settings').then(value => { if (active) setSettings(value); }).catch(() => {}); };
    refreshSettings();
    const timer = window.setInterval(refreshSettings, 15000);
    window.addEventListener('focus', refreshSettings);
    return () => { active = false; window.clearInterval(timer); window.removeEventListener('focus', refreshSettings); };
  }, []);
  useEffect(() => { const media = window.matchMedia('(max-width:760px)'); const change = () => setCompact(media.matches); media.addEventListener('change', change); return () => media.removeEventListener('change', change); }, []);
  useEffect(() => {
    const id = workspace.selectedId || 'new';
    if (composerTask.current !== id) { messageDrafts.current.set(composerTask.current, composerRef.current); setComposer(messageDrafts.current.get(id) || ''); composerTask.current = id; }
    setSelectedNode(null); setSelectedPaper(null); setActionError(''); setPreview(false); setResultTab('report');
  }, [workspace.selectedId]);
  useEffect(() => { document.title = detail?.task.title ? `${detail.task.title} · 科研` : '科研 · 从一个问题开始'; }, [detail?.task.title]);

  const switchTask = async (id: string) => { if (id === workspace.selectedId) return; if (!await editor.save()) { void message.error('请先处理当前文档的版本差异或保存错误。'); return; } workspace.choose(id); };
  const createTask = async () => { if (!await editor.save()) return; setBusy('create'); setActionError(''); try { await workspace.create(); } catch (error) { setActionError(messageOf(error)); } finally { setBusy(''); } };
  const send = async () => {
    const text = composer.trim(); if (!text || busy) return;
    setBusy('message'); setActionError('');
    try {
      if (!await editor.save()) { setActionError('请先处理文档版本差异，再继续对话。'); return; }
      const task = detail || await workspace.create();
      const next = await api<TaskDetail>(taskPath(task.task.id, '/messages'), { text }, 60000);
      workspace.accept(next); setComposer(current => current.trim() === text ? '' : current); messageDrafts.current.delete(task.task.id); setResultTab('report');
    } catch (error) { setActionError(messageOf(error)); }
    finally { setBusy(''); }
  };
  const start = async () => {
    if (!detail || busy) return;
    setBusy('start'); setActionError('');
    try { if (!await editor.save()) return; const latest = await api<TaskDetail>(taskPath(detail.task.id)); workspace.accept(latest); if (latest.document.polishing) { setActionError('需求正在整理，完成后即可开始研究。'); return; } workspace.accept(await api<TaskDetail>(taskPath(detail.task.id, '/start'), { expectedRevision: latest.document.revision }, 60000)); }
    catch (error) { setActionError(messageOf(error)); }
    finally { setBusy(''); }
  };
  const action = async (name: 'pause' | 'resume') => { if (!detail) return; setBusy(name); setActionError(''); try { workspace.accept(await api<TaskDetail>(taskPath(detail.task.id, `/actions/${name}`), {})); } catch (error) { setActionError(messageOf(error)); } finally { setBusy(''); } };
  const exportTask = async () => {
    if (!detail) return;
    setBusy('export'); setActionError('');
    try { const response = await fetch(`/api${taskPath(detail.task.id, '/export')}`); if (!response.ok) throw new Error((await response.json()).error || '导出未完成'); const blob = await response.blob(); const url = URL.createObjectURL(blob); const anchor = document.createElement('a'); anchor.href = url; anchor.download = `科研任务-第${detail.task.round}轮.zip`; document.body.appendChild(anchor); anchor.click(); anchor.remove(); window.setTimeout(() => URL.revokeObjectURL(url), 60000); }
    catch (error) { setActionError(messageOf(error)); }
    finally { setBusy(''); }
  };
  const openRun = async (round: number) => { if (!detail) return; setHistoric({ round, data: null, error: '' }); try { const data = await api<Record<string, unknown>>(taskPath(detail.task.id, `/runs/${round}`)); setHistoric({ round, data, error: '' }); } catch (error) { setHistoric({ round, data: null, error: messageOf(error) }); } };
  const isPreparing = !detail || detail.phase === 'empty' || detail.phase === 'requirements';
  const isEmpty = isPreparing && !detail?.document.markdown && !detail?.messages.length && !detail?.document.polishing;
  const running = detail?.phase === 'retrieving' || detail?.phase === 'researching' || detail?.phase === 'failed';
  const lastUser = detail?.messages.filter(message => message.role === 'user').at(-1);
  const latestReply = detail ? preparationReply(detail.messages) : undefined;
  const tasks = workspace.tasks.filter(task => task.title.toLowerCase().includes(tasksQuery.trim().toLowerCase()));
  const error = actionError || workspace.error || detail?.error;
  const phase = detail?.phase || 'empty';
  const taskListItem = (task: TaskSummary) => <button className={`task-list-item ${task.id === workspace.selectedId ? 'selected' : ''}`} key={task.id} onClick={() => { void switchTask(task.id); }} aria-label={`打开科研任务：${task.title || '新的科研任务'}`}><CommentOutlined /><div><strong><BlurText kind="status" text={task.title || '新的科研任务'} /></strong><span><BlurText kind="status" text={`${taskPhaseLabels[task.phase]}${task.round > 1 ? ` · 第 ${task.round} 轮` : ''}`} /></span></div>{['researching', 'retrieving'].includes(task.phase) && <i className="task-running-dot" />}</button>;

  return <div className="conversation-workspace">
    <aside className="research-sidebar" aria-label="科研任务列表"><div className="workspace-brand"><ApartmentOutlined /><strong>科研</strong></div><Tooltip title={compact ? '新建科研任务' : null} placement="right"><Button className="new-task-button" icon={<PlusOutlined />} type="default" onClick={() => { void createTask(); }} loading={busy === 'create'} aria-label="新建科研任务">{!compact && '新建科研任务'}</Button></Tooltip><div className="task-list-heading"><span>科研任务</span><span><BlurText kind="status" text={String(workspace.tasks.length)} /></span></div>{workspace.tasks.length > 6 && !compact && <Input className="task-search" prefix={<SearchOutlined />} value={tasksQuery} onChange={event => setTasksQuery(event.target.value)} allowClear placeholder="搜索任务" aria-label="搜索科研任务" />}<nav className="task-list">{workspace.listLoading ? <div className="task-list-loading"><Spin size="small" /></div> : tasks.map(task => compact ? <Tooltip key={task.id} title={task.title || '新的科研任务'} placement="right">{taskListItem(task)}</Tooltip> : taskListItem(task))}</nav><div className="sidebar-bottom"><Tooltip title={compact ? (modelReady ? 'DeepSeek 连接设置' : '配置 DeepSeek API Key') : null} placement="right"><Button icon={<SettingOutlined />} type="text" aria-label="配置 DeepSeek API Key" onClick={() => setSettingsOpen(true)}>{!compact && (modelReady ? 'DeepSeek' : '连接 DeepSeek')}</Button></Tooltip>{!compact && <span className="sidebar-local">本地工作区</span>}</div></aside>
    <main className={`conversation-main ${isEmpty ? 'is-empty' : ''}`}>
      <header className="conversation-header"><div className="current-task-title"><strong><BlurText kind="status" text={detail?.task.title || '新的科研任务'} /></strong>{!isEmpty && <span className={`phase-label ${detail?.phase === 'failed' ? 'error' : ''}`}><i /><BlurText kind="status" text={detail?.state?.paused && running ? '已暂停' : taskPhaseLabels[phase]} /></span>}</div><div className="header-actions">{detail?.runs.length ? <Button size="small" type="text" onClick={() => setHistoryOpen(true)}>历史轮次</Button> : null}{running && detail?.state && <Button size="small" type="text" icon={detail.state.paused ? <PlayCircleOutlined /> : <PauseOutlined />} loading={busy === 'pause' || busy === 'resume'} onClick={() => { void action(detail.state!.paused ? 'resume' : 'pause'); }}>{detail.state.paused ? '继续' : '暂停'}</Button>}{!modelReady && <Button size="small" type="text" onClick={() => setSettingsOpen(true)}>配置 DeepSeek API Key</Button>}</div></header>
      {error && <div className="workspace-error"><Alert type="error" showIcon description={<div><span>{error}</span><Button size="small" type="text" icon={<ReloadOutlined />} onClick={() => { setActionError(''); void workspace.refresh(); }}>刷新状态</Button></div>} /></div>}
      {workspace.loading ? <div className="task-loading loading-status" role="status"><Spin size="large" /><span>读取科研任务</span></div> : isEmpty ? <section className="empty-research"><div className="empty-research-intro"><span className="empty-mark"><ApartmentOutlined style={{ fontSize: 30 }} /></span><h1>想研究什么？</h1><p>从一个问题开始，把需求写清楚，再深入研究。</p></div><Composer centered value={composer} onChange={setComposer} onSend={() => { void send(); }} busy={busy === 'message' || busy === 'create'} />{!modelReady && <p className="empty-mode-hint">尚未连接 AI，可以先记录本地需求草稿。<button onClick={() => setSettingsOpen(true)}>配置 DeepSeek API Key</button></p>}</section> : <>
        <div className={`task-content ${isPreparing ? 'preparation-content' : running ? 'live-content' : ''}`}>
          {isPreparing && detail && <div className="preparation-workspace">{lastUser && <div className="latest-user-message"><span>你</span><p>{lastUser.content}</p></div>}{latestReply && <div className="preparation-reply" aria-label="研究助手最新回复"><span>研究助手</span><div><Markdown text={latestReply.content.length > 520 ? `${latestReply.content.slice(0, 520)}…` : latestReply.content} />{latestReply.content.length > 520 && <details><summary>展开完整回复</summary><Markdown text={latestReply.content} /></details>}</div></div>}<section className="requirements-document"><div className="document-toolbar"><div><FileTextOutlined /><strong>研究需求.md</strong><span><BlurText kind="status" text={editor.draft.saving ? '正在保存' : editor.draft.dirty ? '有未保存修改' : detail.document.polishing ? '正在整理' : `已保存 · v${editor.draft.baseRevision}`} /></span></div><Space size={4}><Button size="small" type={preview ? 'text' : 'default'} onClick={() => setPreview(false)}>编辑</Button><Button size="small" type={preview ? 'default' : 'text'} onClick={() => { void editor.save(); setPreview(true); }}>预览</Button></Space></div>{!editor.draft.text && detail.document.polishing ? <div className="document-generating"><Spin /><p>{modelReady ? '正在把你的问题整理成研究需求…' : '正在形成需求草稿…'}</p></div> : preview ? <Markdown text={editor.draft.text} className="document-preview" /> : <BlurChange value={editor.draft.baseRevision}><Input.TextArea className="markdown-editor" aria-label="在线编辑研究需求 Markdown" value={editor.draft.text} onChange={event => editor.edit(event.target.value)} onCompositionStart={() => editor.setComposing(true)} onCompositionEnd={() => editor.setComposing(false)} onBlur={() => { void editor.save(); }} autoSize={{ minRows: 13, maxRows: 32 }} placeholder="研究需求将在这里形成，你可以直接编辑。" /></BlurChange>}{editor.draft.conflict && <div className="document-conflict"><p>{editor.draft.error}</p><Space><Button size="small" onClick={editor.keepLocal}>保留我的修改</Button><Button size="small" type="text" onClick={editor.useServer}>采用服务端版本</Button></Space></div>}{editor.draft.error && !editor.draft.conflict && <Alert type="error" showIcon description={editor.draft.error} />}{detail.document.error && <Alert type="warning" showIcon description={detail.document.error} />}<div className="document-bottom"><span>{detail.document.source === 'model' ? '编辑停顿后自动保存，AI 继续润色。' : '本地需求草稿 · 尚未使用 AI 润色。'}</span>{detail.document.polishing && <Spin size="small" />}</div></section>{detail.document.questions.length > 0 && <details className="requirements-questions"><summary>可以进一步补充的 {detail.document.questions.length} 个问题</summary><ul>{detail.document.questions.map((question, index) => <li key={index}>{question}</li>)}</ul></details>}<div className="start-research-row"><span>{modelReady ? '开始后自动检索、建图并推进研究。' : '当前将执行本地资料核验。连接 DeepSeek 后可进行模型研究。'}</span><Button type="primary" size="large" loading={busy === 'start'} disabled={!editor.draft.text.trim() || editor.draft.dirty || editor.draft.saving || editor.draft.conflict || detail.document.polishing || Boolean(busy)} onClick={() => { void start(); }}>开始研究</Button></div></div>}
          {running && detail && <><Suspense fallback={<div className="graph-loading" role="status"><Spin /><span>加载三维结构</span></div>}><ResearchGraph key={detail.task.id} state={detail.state} retrieving={detail.phase === 'retrieving'} onSelect={setSelectedNode} /></Suspense><AgentActivity detail={detail} />{detail.phase === 'failed' && <p className="research-failure-hint">打开失败节点查看原因并重试，或在下方补充问题调整需求。</p>}</>}
          {detail?.phase === 'completed' && <ResultView detail={detail} tab={resultTab} onTab={setResultTab} onPaper={setSelectedPaper} onNode={setSelectedNode} onExport={() => { void exportTask(); }} />}
        </div><div className="bottom-composer"><Composer value={composer} onChange={setComposer} onSend={() => { void send(); }} busy={busy === 'message' || busy === 'create'} phase={detail?.phase} /></div>
      </>}
    </main>
    <DeepSeekSettings open={settingsOpen} settings={settings} onClose={() => setSettingsOpen(false)} onSaved={value => { setSettings(value); void workspace.refresh(); }} />
    {detail && <><TaskNodeDrawer target={selectedNode} detail={detail} onClose={() => setSelectedNode(null)} onPaper={setSelectedPaper} accept={workspace.accept} /><TaskPaperDrawer paper={detail.state?.papers.find(paper => paper.id === selectedPaper)} taskId={detail.task.id} state={detail.state} onClose={() => setSelectedPaper(null)} onPaper={setSelectedPaper} /><Drawer open={historyOpen} title="历史轮次" size={480} onClose={() => setHistoryOpen(false)} rootClassName="task-drawer"><div className="run-list">{[...detail.runs].reverse().map(run => <button key={`${run.round}-${run.at}`} onClick={() => { void openRun(run.round); }}><div><strong>第 {run.round} 轮</strong><span>{timeLabel(run.at)}</span></div><p>{run.summary || '查看本轮结果'}</p><small>{run.mode === 'llm' ? '模型研究' : '本地资料核验'}</small></button>)}</div></Drawer><Modal open={Boolean(historic)} title={`第 ${historic?.round || ''} 轮研究结果`} width={800} footer={<Button onClick={() => setHistoric(null)}>关闭</Button>} onCancel={() => setHistoric(null)}>{historic?.error ? <Alert type="error" showIcon description={historic.error} /> : historic?.data ? <HistoricalReport data={historic.data} /> : <div className="loading-status" role="status"><Spin /><span>读取历史结果</span></div>}</Modal></>}
  </div>;
}

class Boundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  render() { return this.state.failed ? <div className="fatal-workspace"><h2>界面暂时遇到问题</h2><p>任务与研究结果仍保存在本地服务中。</p><Button type="primary" onClick={() => window.location.reload()}>重新载入</Button></div> : this.props.children; }
}
export default function App() { return <Boundary><Workspace /></Boundary>; }
