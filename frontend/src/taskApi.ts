import { useCallback, useEffect, useRef, useState } from 'react';
import type { TaskDetail, TaskSummary } from './taskTypes';

export class ApiError extends Error { constructor(message: string, public status: number) { super(message); this.name = 'ApiError'; } }
export const messageOf = (error: unknown) => error instanceof Error ? error.message : '操作未完成，请重试。';
export async function api<T>(path: string, body?: unknown, timeoutMs = 30000): Promise<T> {
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(`/api${path}`, { method: body === undefined ? 'GET' : 'POST', headers: { 'Content-Type': 'application/json' }, ...(body === undefined ? {} : { body: JSON.stringify(body) }), signal: controller.signal });
    let value: unknown;
    try { value = await response.json(); } catch { throw new ApiError('服务返回了无法读取的数据。', response.status); }
    if (!response.ok) throw new ApiError((value as { error?: string }).error || `请求失败（${response.status}）`, response.status);
    return value as T;
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') throw new ApiError('请求超时。已有研究在本地服务中继续保留，请刷新状态。', 0);
    if (error instanceof TypeError) throw new ApiError('无法连接本地科研服务，请确认服务仍在运行。', 0);
    throw error;
  } finally { window.clearTimeout(timeout); }
}
export const taskPath = (id: string, path = '') => `/tasks/${encodeURIComponent(id)}${path}`;
const selectedFromHash = () => { const match = window.location.hash.match(/^#task\/(.+)$/); return match ? decodeURIComponent(match[1]) : null; };

export function useTaskWorkspace() {
  const [tasks, setTasks] = useState<TaskSummary[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(selectedFromHash);
  const [detail, setDetail] = useState<TaskDetail | null>(null);
  const [loading, setLoading] = useState(Boolean(selectedId));
  const [listLoading, setListLoading] = useState(true);
  const [error, setError] = useState('');
  const selected = useRef(selectedId);
  const sequence = useRef(0);
  const applied = useRef(0);
  const inflight = useRef<string | null>(null);
  const mounted = useRef(true);
  selected.current = selectedId;

  const refreshList = useCallback(async () => {
    try { const value = await api<{ tasks: TaskSummary[] }>('/tasks'); if (mounted.current) { setTasks(value.tasks); setError(''); } }
    catch (error) { if (mounted.current) setError(messageOf(error)); }
    finally { if (mounted.current) setListLoading(false); }
  }, []);
  const choose = useCallback((id: string | null) => {
    selected.current = id; sequence.current += 1; applied.current = sequence.current;
    setSelectedId(id); setDetail(null); setLoading(Boolean(id)); setError('');
    window.history.replaceState(null, '', id ? `#task/${encodeURIComponent(id)}` : window.location.pathname);
  }, []);
  const accept = useCallback((next: TaskDetail) => {
    if (!mounted.current || next.task.id !== selected.current) return;
    sequence.current += 1; applied.current = sequence.current;
    setDetail(next); setLoading(false); setError('');
    setTasks(previous => [next.task, ...previous.filter(task => task.id !== next.task.id)].sort((a, b) => b.updatedAt.localeCompare(a.updatedAt)));
  }, []);
  const refresh = useCallback(async () => {
    const id = selected.current;
    if (!id || inflight.current === id) return;
    inflight.current = id; const requestSequence = ++sequence.current;
    try {
      const value = await api<TaskDetail>(taskPath(id));
      if (mounted.current && selected.current === id && requestSequence >= applied.current) {
        applied.current = requestSequence; setDetail(value); setLoading(false); setError('');
        setTasks(previous => previous.some(task => task.id === id) ? previous.map(task => task.id === id ? value.task : task) : [value.task, ...previous]);
      }
    } catch (error) { if (mounted.current && selected.current === id) { setError(messageOf(error)); setLoading(false); } }
    finally { if (inflight.current === id) inflight.current = null; }
  }, []);
  useEffect(() => { mounted.current = true; void refreshList(); const timer = window.setInterval(() => { void refreshList(); }, 10000); return () => { mounted.current = false; window.clearInterval(timer); }; }, [refreshList]);
  useEffect(() => { if (!selectedId) return; void refresh(); const timer = window.setInterval(() => { void refresh(); }, 1500); return () => window.clearInterval(timer); }, [selectedId, refresh]);
  useEffect(() => { const handle = () => { const id = selectedFromHash(); if (id !== selected.current) choose(id); }; window.addEventListener('hashchange', handle); return () => window.removeEventListener('hashchange', handle); }, [choose]);
  const create = useCallback(async () => { const next = await api<TaskDetail>('/tasks', {}); choose(next.task.id); accept(next); return next; }, [choose, accept]);
  return { tasks, selectedId, detail, loading, listLoading, error, choose, accept, refresh, refreshList, create };
}
