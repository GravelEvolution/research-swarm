import { useEffect, useState } from 'react';
import { Alert, App as AntdApp, Button, Checkbox, Input, Modal, Select } from 'antd';
import { api, messageOf, taskPath } from './taskApi';
import { isNodeImpactCurrent, nodeActionFingerprint, nodeActionRequest, type ReviewedNodeImpact } from './nodeActionPayload';
import type { TaskDetail } from './taskTypes';
import type { ActionKind, Impact, ResearchNode } from './types';

const labels: Record<ActionKind, string> = { deepen: '深入研究', modify: '修改节点需求', insert: '插入子任务', reject: '否决节点结论' };
const placeholders: Record<ActionKind, string> = { deepen: '还有哪个问题值得进一步研究？', modify: '修改此节点的任务描述或约束…', insert: '描述希望在此节点下新增的研究任务…', reject: '说明不接受此节点结论的原因…' };
const confirmLabels: Record<ActionKind, string> = { deepen: '确认并深入研究', modify: '确认修改并重跑', insert: '确认插入并重跑', reject: '确认否决并更新汇总' };

export function TaskNodeActionModal({ initialKind, node, detail, onClose, accept }: { initialKind: ActionKind | null; node?: ResearchNode; detail: TaskDetail; onClose: () => void; accept: (detail: TaskDetail) => void }) {
  const { message } = AntdApp.useApp();
  const [kind, setKind] = useState<ActionKind>('deepen');
  const [text, setText] = useState('');
  const [query, setQuery] = useState('');
  const [allowNewSearch, setAllowNewSearch] = useState(true);
  const [review, setReview] = useState<ReviewedNodeImpact | null>(null);
  const [busy, setBusy] = useState<'preview' | 'apply' | ''>('');
  const [error, setError] = useState('');
  useEffect(() => {
    if (!initialKind) return;
    setKind(initialKind); setText(initialKind === 'modify' ? String(node?.input.description || '') : ''); setQuery(''); setAllowNewSearch(true); setReview(null); setError('');
  }, [initialKind, node?.id, detail.task.id]);
  const draft = { nodeId: node?.id || '', kind, text, query, allowNewSearch };
  const current = isNodeImpactCurrent(review, draft, detail.state?.revision ?? -1);
  const changed = () => { setReview(null); setError(''); };
  const preview = async () => {
    if (!node || !text.trim()) return;
    setBusy('preview'); setError('');
    try { const impact = await api<Impact>(taskPath(detail.task.id, '/actions/impact'), { nodeId: node.id, kind }); setReview({ impact, fingerprint: nodeActionFingerprint(draft) }); }
    catch (error) { setError(messageOf(error)); }
    finally { setBusy(''); }
  };
  const apply = async () => {
    if (!node) return;
    setBusy('apply'); setError('');
    try {
      const request = nodeActionRequest(draft, review, detail.state?.revision ?? -1);
      accept(await api<TaskDetail>(taskPath(detail.task.id, request.path), request.body, kind === 'deepen' ? 600000 : 60000));
      void message.success(`${labels[kind]}已提交`); onClose();
    } catch (error) { setError(messageOf(error)); if (/版本|revision|预览/i.test(messageOf(error))) setReview(null); }
    finally { setBusy(''); }
  };
  return <Modal open={Boolean(initialKind && node)} title={labels[kind]} width={590} mask={{ closable: !busy }} closable={!busy} keyboard={!busy} onCancel={() => { if (!busy) onClose(); }} footer={<div className="node-action-footer"><Button disabled={Boolean(busy)} onClick={onClose}>取消</Button><Button type={current ? 'text' : 'primary'} disabled={!text.trim() || Boolean(busy)} loading={busy === 'preview'} onClick={() => { void preview(); }}>{review ? '刷新影响预览' : '预览影响'}</Button>{review && <Button type="primary" danger={kind === 'reject'} disabled={!current || Boolean(busy)} loading={busy === 'apply'} onClick={() => { void apply(); }}>{confirmLabels[kind]}</Button>}</div>}>
    <p className="quiet-text">{node?.title}</p>
    <Select<ActionKind> value={kind} onChange={next => { setKind(next); setText(next === 'modify' ? String(node?.input.description || '') : ''); changed(); }} options={Object.entries(labels).map(([value, label]) => ({ value, label }))} aria-label="节点调整方式" disabled={Boolean(busy)} className="node-action-kind" />
    <Input.TextArea value={text} onChange={event => { setText(event.target.value); changed(); }} autoSize={{ minRows: 4, maxRows: 8 }} aria-label={kind === 'deepen' ? '深入研究问题' : '节点调整说明'} placeholder={placeholders[kind]} disabled={Boolean(busy)} />
    {kind === 'deepen' && <><div className="deep-search-option"><Checkbox checked={allowNewSearch} onChange={event => { setAllowNewSearch(event.target.checked); changed(); }} disabled={Boolean(busy)}>允许研究节点补充检索相关论文</Checkbox></div><details className="query-option"><summary>指定检索词（可选）</summary><Input value={query} onChange={event => { setQuery(event.target.value); changed(); }} placeholder="留空由研究节点决定检索方向" aria-label="深入研究检索词" disabled={Boolean(busy)} /></details></>}
    {review && <section className="node-impact-preview" aria-label="节点操作影响预览"><strong>将影响 {review.impact.affectedIds.length} 个节点</strong><p>这些节点的结果会更新，相关汇总将重新计算。原始执行记录保留在历史中。</p><ul>{review.impact.affectedIds.map(id => <li key={id}>{detail.state?.nodes.find(item => item.id === id)?.title || id}</li>)}</ul>{!current && <Alert type="warning" showIcon description="研究状态已更新，请刷新影响预览后再确认。" />}</section>}
    {busy === 'apply' && <p className="quiet-text">正在提交调整，新的结果会出现在任务中。</p>}{error && <Alert type="error" showIcon description={error} />}
  </Modal>;
}
