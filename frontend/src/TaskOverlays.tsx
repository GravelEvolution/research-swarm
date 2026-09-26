import { useEffect, useState } from 'react';
import { Alert, App as AntdApp, Button, Drawer, Dropdown, Input, Space, Spin, Tabs, Tag, type TabsProps } from 'antd';
import { DownloadOutlined, SearchOutlined } from '@ant-design/icons';
import { api, messageOf, taskPath } from './taskApi';
import type { TaskDetail } from './taskTypes';
import type { ActionKind, Evidence, Paper, Settings, Snapshot } from './types';
import type { VisualNode } from './graphData';
import { Markdown } from './Markdown';
import { BlurText } from './BlurReveal';
import { TaskNodeActionModal } from './TaskNodeActionModal';

const formatTime = (at?: string | null) => at ? new Date(at).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false }) : '—';
const statusText = { pending: '待执行', running: '运行中', completed: '已完成', failed: '失败', waiting_user: '待处理' };
const phaseText: Record<string, string> = { plan: '规划', execute: '执行', aggregate: '汇总' };
const safeUrl = (url: string) => /^https?:\/\//i.test(url) || /^\/api\//.test(url) ? url : undefined;

export function TaskEvidence({ evidence, state, onPaper }: { evidence: Evidence[]; state: Snapshot; onPaper: (id: string) => void }) {
  if (!evidence.length) return <p className="unsupported-note">无可定位证据。此项仍是待验证判断。</p>;
  return <div className="task-evidence-list">{evidence.map(item => {
    const paper = state.papers.find(paper => paper.id === item.paperId);
    const type = item.type === 'experiment' ? '实验产物' : /abstract|summary/i.test(item.type) ? '摘要证据' : /full/i.test(item.type) ? '全文证据' : item.type || '资料证据';
    return <article key={item.id}><div><Tag>{type}</Tag><span>证据 #{item.id}</span></div><blockquote>{item.quote || '未提供原文摘录'}</blockquote>{paper && <button className="inline-link" onClick={() => onPaper(paper.id)}>{paper.title}</button>}<p className="evidence-locator">{item.locator || '未定位'}{item.extractor ? ` · ${item.extractor}` : ''}</p></article>;
  })}</div>;
}

export function TaskNodeDrawer({ target, detail, onClose, onPaper, accept }: { target: VisualNode | null; detail: TaskDetail; onClose: () => void; onPaper: (id: string) => void; accept: (detail: TaskDetail) => void }) {
  const { message } = AntdApp.useApp();
  const state = detail.state;
  const node = state?.nodes.find(node => node.id === target?.nodeId);
  const facet = state?.facetNodes.find(facet => facet.id === target?.facetId);
  const [tab, setTab] = useState('output');
  const [runs, setRuns] = useState<Record<string, unknown>[]>([]);
  const [historyError, setHistoryError] = useState('');
  const [historyLoading, setHistoryLoading] = useState(false);
  const [actionKind, setActionKind] = useState<ActionKind | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => { setTab('output'); setRuns([]); setHistoryError(''); setActionKind(null); }, [target?.id, detail.task.id]);
  useEffect(() => {
    if (!node || tab !== 'history') return;
    let active = true; setHistoryLoading(true);
    api<{ runs?: Record<string, unknown>[]; executions?: Record<string, unknown>[] }>(taskPath(detail.task.id, `/nodes/${encodeURIComponent(node.id)}/history`)).then(value => { if (active) setRuns(value.runs || value.executions || []); }).catch(error => { if (active) setHistoryError(messageOf(error)); }).finally(() => { if (active) setHistoryLoading(false); });
    return () => { active = false; };
  }, [node?.id, node?.version, node?.finishedAt, tab, detail.task.id]);
  const retry = async () => { if (!node) return; setBusy(true); try { accept(await api<TaskDetail>(taskPath(detail.task.id, '/actions/retry'), { nodeId: node.id })); } catch (error) { void message.error(messageOf(error)); } finally { setBusy(false); } };
  const ids = new Set([...(node?.evidenceIds || []), ...(node?.output?.evidenceIds || []), ...(node?.output?.claims?.flatMap(claim => claim.evidenceIds) || [])]);
  const tabs: TabsProps['items'] = node && state ? [
    {
      key: 'output',
      label: '输出与依据',
      children: <div className="drawer-section">{node.output ? <>
        <Markdown text={node.output.summary || '暂无摘要'} />
        {node.output.claims?.map(claim => <div className="node-claim-v2" key={claim.id}><p>{claim.text}</p>{!claim.evidenceIds.length && <span className="unsupported-note">无证据</span>}{claim.limitations && <p className="quiet-text">{claim.limitations}</p>}</div>)}
        <h3>引用证据</h3><TaskEvidence evidence={state.evidence.filter(item => ids.has(item.id))} state={state} onPaper={onPaper} />
        {!!node.output.unresolved?.length && <><h3>尚未解决</h3><ul>{node.output.unresolved.map((item, index) => <li key={index}>{item}</li>)}</ul></>}
        {node.output.structured && <details className="raw-details"><summary>结构化产物</summary><pre>{JSON.stringify(node.output.structured, null, 2)}</pre></details>}
      </> : <p className="quiet-text">节点尚未产生有效输出。可在动作记录查看执行情况。</p>}</div>,
    },
    {
      key: 'input',
      label: '任务输入',
      children: <div className="drawer-section">{Object.entries(node.input).map(([key, value]) => <div className="task-input" key={key}><span>{({ description: '任务描述', acceptance: '完成标准', constraints: '约束', experiment: '实验输入', paperIds: '关联论文' } as Record<string, string>)[key] || key}</span><pre>{typeof value === 'string' ? value : JSON.stringify(value, null, 2)}</pre></div>)}</div>,
    },
    {
      key: 'logs',
      label: '动作记录',
      children: <><ol className="node-log-list">{node.logs.map(log => <li key={log.id}><time>{formatTime(log.at)}</time><p>{log.message}</p></li>)}</ol>{!node.logs.length && <p className="quiet-text">暂无动作记录。</p>}</>,
    },
    {
      key: 'history',
      label: '历史执行',
      children: <div className="drawer-section">{historyLoading ? <div className="loading-status" role="status"><Spin /><span>读取历史执行</span></div> : historyError ? <Alert type="error" description={historyError} showIcon /> : runs.length ? [...runs].reverse().map((run, index) => <details className="raw-details" key={String(run.id || index)}><summary>第 {String(run.round || '—')} 轮 · v{String(run.version || '—')} · {run.valid === false ? '已失效' : '执行记录'} · {formatTime(String(run.at || ''))}</summary><pre>{JSON.stringify({ input: run.input, output: run.output }, null, 2)}</pre></details>) : <p className="quiet-text">暂无历史执行。</p>}</div>,
    },
  ] : [];

  return <>
    <Drawer open={Boolean(target)} title="研究节点" size={640} onClose={onClose} rootClassName="task-drawer">
      {target && state && <>
        <div className="node-detail-heading"><span><BlurText kind="status" text={node ? `${statusText[node.status]} · ${phaseText[node.phase] || node.phase} · v${node.version}` : facet?.facetName || '研究切面'} /></span><h2><BlurText text={node?.title || facet?.title || target.title} /></h2><p><BlurText text={node?.role || target.action} /></p></div>
        {node && <div className="node-detail-actions">
          <Button icon={<SearchOutlined />} type="primary" onClick={() => setActionKind('deepen')}>从这里深入研究</Button>
          <Dropdown trigger={['click']} placement="bottomLeft" menu={{
            items: [
              { key: 'modify', label: '修改节点需求' },
              { key: 'insert', label: '插入子任务' },
              { key: 'reject', label: '否决节点结论', danger: true },
            ],
            onClick: ({ key }) => setActionKind(key as ActionKind),
          }}><Button type="text" aria-label="调整研究节点">调整节点</Button></Dropdown>
          {node.status === 'failed' && <Button loading={busy} onClick={() => { void retry(); }}>重试节点</Button>}
          <span>{node.elapsedMs > 1000 ? `${(node.elapsedMs / 1000).toFixed(1)} 秒` : `${node.elapsedMs || 0} 毫秒`}</span>
        </div>}
        {node ? <Tabs activeKey={tab} onChange={setTab} items={tabs} /> : <div className="drawer-section"><h3>关联论文</h3>{state.papers.filter(paper => facet?.paperIds.includes(paper.id)).map(paper => <button className="paper-list-link" key={paper.id} onClick={() => onPaper(paper.id)}>{paper.title}<span>{paper.year || '年份未标注'}</span></button>)}</div>}
      </>}
    </Drawer>
    <TaskNodeActionModal initialKind={actionKind} node={node} detail={detail} onClose={() => setActionKind(null)} accept={accept} />
  </>;
}

export function TaskPaperDrawer({ paper, taskId, state, onClose, onPaper }: { paper?: Paper; taskId: string; state: Snapshot | null; onClose: () => void; onPaper: (id: string) => void }) {
  const tabs: TabsProps['items'] = paper && state ? [
    {
      key: 'evidence',
      label: '证据',
      children: <div className="drawer-section"><TaskEvidence evidence={state.evidence.filter(item => item.paperId === paper.id || paper.evidenceIds.includes(item.id))} state={state} onPaper={onPaper} /></div>,
    },
    {
      key: 'abstract',
      label: '摘要与材料',
      children: <div className="drawer-section"><Markdown text={paper.abstract || '来源未提供摘要。'} /><h3>复现材料</h3><p>{paper.reproducibility || '材料待核对；不代表已经完成复现实验。'}</p></div>,
    },
  ] : [];
  return <Drawer open={Boolean(paper)} title="论文与证据" size={650} onClose={onClose} rootClassName="task-drawer">{paper && state && <>
    <div className="node-detail-heading"><span>{paper.year || '年份未标注'} · {paper.venue || '来源未标注'} · 论文 #{paper.id}</span><h2>{paper.title}</h2><p>{Array.isArray(paper.authors) ? paper.authors.join('，') : paper.authors}</p></div>
    <div className="paper-source-actions">{paper.pdfAvailable && <Button icon={<DownloadOutlined />} onClick={() => window.open(`/api${taskPath(taskId, `/papers/${encodeURIComponent(paper.id)}/pdf`)}`, '_blank', 'noopener,noreferrer')}>查看全文</Button>}{paper.codeUrl && safeUrl(paper.codeUrl) && <a href={safeUrl(paper.codeUrl)} target="_blank" rel="noreferrer">代码线索 ↗</a>}{paper.doi && <a href={safeUrl(paper.doi) || `https://doi.org/${encodeURIComponent(paper.doi)}`} target="_blank" rel="noreferrer">来源 ↗</a>}</div>
    <Tabs items={tabs} />
  </>}</Drawer>;
}

export function DeepSeekSettings({ open, settings, onClose, onSaved }: { open: boolean; settings: Settings | null; onClose: () => void; onSaved: (settings: Settings) => void }) {
  const { message } = AntdApp.useApp();
  const [key, setKey] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => { if (open) { setKey(''); setError(''); } }, [open]);
  const save = async () => {
    if (!key.trim()) return;
    setBusy(true); setError('');
    try { const result = await api<{ ok?: boolean; error?: string; message?: string }>('/setup', { apiKey: key.trim() }, 120000); if (result.ok === false) throw new Error(result.error || result.message || '连接测试未通过'); onSaved(await api<Settings>('/settings')); setKey(''); void message.success('DeepSeek 已连接'); onClose(); }
    catch (error) { setError(messageOf(error)); }
    finally { setBusy(false); }
  };
  return <Drawer open={open} title="连接 DeepSeek" size={440} onClose={onClose} rootClassName="task-drawer" footer={<Space><Button onClick={onClose}>关闭</Button><Button type="primary" loading={busy} disabled={!key.trim()} onClick={() => { void save(); }}>保存并测试</Button></Space>}>
    <p className="settings-intro">填入一条 API Key，即可让 AI 整理需求、检索论文并继续研究。</p>
    {settings?.provider.hasKey && <Tag color="green">已保存密钥</Tag>}
    <label className="simple-label" htmlFor="deepseek-key">DeepSeek API Key</label>
    <Input.Password id="deepseek-key" value={key} onChange={event => setKey(event.target.value)} autoComplete="new-password" placeholder={settings?.provider.hasKey ? '输入新密钥以更新连接' : 'sk-…'} disabled={busy} />
    <p className="quiet-text">密钥保存在本机。模型和服务地址已配置，无需填写其他账号或参数。</p>
    {error && <Alert type="error" description={error} showIcon />}
  </Drawer>;
}
