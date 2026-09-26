import { useCallback, useEffect, useRef, useState } from 'react';
import { api, ApiError, messageOf, taskPath } from './taskApi';
import { draftReducer, emptyDraft, type DraftAction } from './documentState';
import type { TaskDetail } from './taskTypes';

export function useDocumentDraft(detail: TaskDetail | null, accept: (detail: TaskDetail) => void) {
  const [draft, setDraft] = useState(emptyDraft);
  const current = useRef(draft);
  const pending = useRef<Promise<boolean> | null>(null);
  const [composing, setComposing] = useState(false);
  const update = useCallback((action: DraftAction) => { current.current = draftReducer(current.current, action); setDraft(current.current); }, []);
  useEffect(() => {
    const id = detail?.task.id || null;
    if (current.current.taskId !== id) update({ type: 'reset', taskId: id, document: detail?.document || null });
    else if (detail) update({ type: 'server', document: detail.document });
  }, [detail?.task.id, detail?.document.revision, detail?.document.markdown, detail?.document.polishing, detail?.document.polishedFrom, detail?.document.error, update]);

  const save = useCallback(async (): Promise<boolean> => {
    if (pending.current) { const okay = await pending.current; if (!okay) return false; }
    const value = current.current;
    if (!value.dirty) return true;
    if (!value.taskId || value.conflict) return false;
    const taskId = value.taskId;
    update({ type: 'saving' });
    const work = (async () => {
      try {
        const next = await api<TaskDetail>(taskPath(taskId, '/document'), { markdown: value.text, expectedRevision: value.baseRevision });
        if (current.current.taskId === taskId) { update({ type: 'saved', document: next.document }); accept(next); }
        return true;
      } catch (error) {
        if (current.current.taskId === taskId) {
          update({ type: 'failed', error: messageOf(error), conflict: error instanceof ApiError && error.status === 409 });
          if (error instanceof ApiError && error.status === 409) {
            try { const next = await api<TaskDetail>(taskPath(taskId)); if (current.current.taskId === taskId) { update({ type: 'server', document: next.document }); accept(next); } } catch { /* The original conflict stays visible and the local text remains intact. */ }
          }
        }
        return false;
      } finally { pending.current = null; }
    })();
    pending.current = work;
    return work;
  }, [accept, update]);
  const editable = detail?.phase === 'requirements' || detail?.phase === 'empty';
  useEffect(() => {
    if (!editable || !draft.dirty || draft.saving || draft.conflict || draft.error || composing) return;
    const timer = window.setTimeout(() => { void save(); }, 1100);
    return () => window.clearTimeout(timer);
  }, [editable, draft.editVersion, draft.baseRevision, draft.dirty, draft.saving, draft.conflict, draft.error, composing, save]);
  useEffect(() => {
    if (!draft.dirty) return;
    const handler = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ''; };
    window.addEventListener('beforeunload', handler);
    return () => window.removeEventListener('beforeunload', handler);
  }, [draft.dirty]);
  return { draft, save, setComposing, edit: (text: string) => update({ type: 'edit', text }), keepLocal: () => update({ type: 'keep-local' }), useServer: () => update({ type: 'use-server' }) };
}
