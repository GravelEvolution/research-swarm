import type { ResearchDocument } from './taskTypes';

export interface DraftState {
  taskId: string | null;
  text: string;
  baseRevision: number;
  editVersion: number;
  dirty: boolean;
  saving: boolean;
  conflict: boolean;
  error: string;
  server: ResearchDocument | null;
  submitted: { text: string; editVersion: number } | null;
  lastSavedRevision: number | null;
  lastSavedText: string | null;
}
export const emptyDraft = (): DraftState => ({ taskId: null, text: '', baseRevision: 0, editVersion: 0, dirty: false, saving: false, conflict: false, error: '', server: null, submitted: null, lastSavedRevision: null, lastSavedText: null });
export type DraftAction =
  | { type: 'reset'; taskId: string | null; document: ResearchDocument | null }
  | { type: 'server'; document: ResearchDocument }
  | { type: 'edit'; text: string }
  | { type: 'saving' }
  | { type: 'saved'; document: ResearchDocument }
  | { type: 'failed'; error: string; conflict: boolean }
  | { type: 'keep-local' }
  | { type: 'use-server' };

function serverUpdate(state: DraftState, document: ResearchDocument): DraftState {
  if (document.revision < (state.server?.revision ?? -1)) return state;
  const next = { ...state, server: document };
  if (state.saving) return next;
  if (!state.dirty) return { ...next, text: document.markdown, baseRevision: document.revision, error: '', conflict: false };
  if (document.revision <= state.baseRevision) return next;
  const ownPolish = state.lastSavedRevision !== null && document.polishedFrom === state.lastSavedRevision;
  if (ownPolish) return { ...next, baseRevision: document.revision };
  return { ...next, conflict: true, error: '文档有新的服务端版本。你的输入已保留，请选择如何继续。' };
}

export function draftReducer(state: DraftState, action: DraftAction): DraftState {
  if (action.type === 'reset') return { ...emptyDraft(), taskId: action.taskId, text: action.document?.markdown || '', baseRevision: action.document?.revision || 0, server: action.document };
  if (action.type === 'server') return serverUpdate(state, action.document);
  if (action.type === 'edit') return { ...state, text: action.text, dirty: true, editVersion: state.editVersion + 1, error: state.conflict ? state.error : '' };
  if (action.type === 'saving') return { ...state, saving: true, error: '', submitted: { text: state.text, editVersion: state.editVersion } };
  if (action.type === 'failed') return { ...state, saving: false, error: action.error, conflict: action.conflict || state.conflict, submitted: null };
  if (action.type === 'saved') {
    const sent = state.submitted;
    const newerEdits = Boolean(sent && state.editVersion !== sent.editVersion);
    const document = state.server && state.server.revision > action.document.revision ? state.server : action.document;
    const next: DraftState = { ...state, saving: false, dirty: newerEdits, conflict: false, error: '', text: newerEdits ? state.text : document.markdown, baseRevision: action.document.revision, server: null, submitted: null, lastSavedRevision: action.document.revision, lastSavedText: sent?.text ?? action.document.markdown };
    return serverUpdate(next, document);
  }
  if (action.type === 'keep-local') return { ...state, baseRevision: state.server?.revision ?? state.baseRevision, dirty: true, conflict: false, error: '' };
  if (action.type === 'use-server' && state.server) return { ...state, text: state.server.markdown, baseRevision: state.server.revision, dirty: false, conflict: false, error: '' };
  return state;
}
