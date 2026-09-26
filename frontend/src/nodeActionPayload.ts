import type { ActionKind, Impact } from './types';

export interface NodeActionDraft { nodeId: string; kind: ActionKind; text: string; query: string; allowNewSearch: boolean }
export interface ReviewedNodeImpact { impact: Impact; fingerprint: string }
export const nodeActionFingerprint = (draft: NodeActionDraft) => JSON.stringify({ nodeId: draft.nodeId, kind: draft.kind, text: draft.text.trim(), ...(draft.kind === 'deepen' ? { query: draft.query.trim(), allowNewSearch: draft.allowNewSearch } : {}) });
export const isNodeImpactCurrent = (review: ReviewedNodeImpact | null, draft: NodeActionDraft, revision: number) => Boolean(review && review.impact.revision === revision && review.fingerprint === nodeActionFingerprint(draft));

export function nodeActionRequest(draft: NodeActionDraft, review: ReviewedNodeImpact | null, revision: number) {
  if (!draft.text.trim()) throw new Error('请填写新的任务描述或调整原因。');
  if (!isNodeImpactCurrent(review, draft, revision)) throw new Error('请先查看当前操作的最新影响预览。');
  return { path: draft.kind === 'deepen' ? '/deepen' : '/actions/intervene', body: { nodeId: draft.nodeId, kind: draft.kind, text: draft.text.trim(), expectedRevision: review!.impact.revision, ...(draft.kind === 'deepen' ? { query: draft.query.trim(), topK: 10, allowNewSearch: draft.allowNewSearch } : {}) } };
}
