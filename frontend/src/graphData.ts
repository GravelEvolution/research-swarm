import type { Snapshot, NodeStatus } from './types';

export interface VisualNode {
  id: string;
  title: string;
  status: NodeStatus;
  action: string;
  sourceKind: 'agent' | 'facet';
  nodeId?: string;
  parentId?: string | null;
  facetId?: string;
  active: boolean;
  x?: number;
  y?: number;
  z?: number;
  fx?: number;
  fy?: number;
  fz?: number;
  vx?: number;
  vy?: number;
  vz?: number;
}
export interface VisualLink { source: string | VisualNode; target: string | VisualNode; type: string; reason: string }
export interface ResearchGraphData { nodes: VisualNode[]; links: VisualLink[] }

export function mergeGraphLinkReasons(current: VisualLink[], incoming: VisualLink[]) {
  const id = (node: string | VisualNode) => typeof node === 'string' ? node : node.id;
  const key = (link: VisualLink) => `${id(link.source)}|${id(link.target)}|${link.type}`;
  const metadata = new Map(incoming.map(link => [key(link), link.reason]));
  for (const link of current) if (metadata.has(key(link))) link.reason = metadata.get(key(link))!;
}

export function mergeGraphNodes(incoming: VisualNode[], existing: Map<string, VisualNode>): VisualNode[] {
  return incoming.map(node => {
    const previous = existing.get(node.id);
    return previous ? Object.assign(previous, node) : { ...node };
  });
}

export function buildGraphData(state: Snapshot | null): ResearchGraphData {
  if (!state) return { nodes: [], links: [] };
  const nodes: VisualNode[] = state.nodes.map(node => ({ id: node.id, title: node.title, status: node.status, action: node.logs.at(-1)?.message || node.output?.summary || ({ plan: '规划研究任务', execute: '执行研究任务', aggregate: '汇总子任务结果' } as Record<string, string>)[node.phase] || '等待调度', sourceKind: 'agent', nodeId: node.id, parentId: node.parentId, facetId: node.sourceNodeId || undefined, active: node.active }));
  const facetAgents = new Map(state.nodes.filter(node => node.sourceNodeId).map(node => [node.sourceNodeId!, node.id]));
  state.facetNodes.forEach(facet => {
    if (!facetAgents.has(facet.id)) {
      const id = `facet:${facet.id}`;
      nodes.push({ id, title: facet.title, status: 'pending', action: `${facet.facetName} · ${facet.paperIds.length} 篇关联论文`, sourceKind: 'facet', facetId: facet.id, active: false });
      facetAgents.set(facet.id, id);
    }
  });
  const nodeIds = new Set(nodes.map(node => node.id));
  const links: VisualLink[] = [];
  const seen = new Set<string>();
  const add = (source: string | undefined | null, target: string | undefined | null, type: string, reason: string) => {
    if (!source || !target || source === target || !nodeIds.has(source) || !nodeIds.has(target)) return;
    const key = `${source}|${target}|${type}`;
    if (seen.has(key)) return;
    seen.add(key); links.push({ source, target, type, reason });
  };
  state.edges.forEach(edge => add(edge.source, edge.target, edge.type, edge.reason));
  state.nodes.forEach(node => { if (node.parentId && !state.edges.some(edge => edge.source === node.parentId && edge.target === node.id)) add(node.parentId, node.id, 'decompose', '父子研究任务'); });
  state.facetNodes.forEach(facet => { if (facet.parentId) add(facetAgents.get(facet.parentId), facetAgents.get(facet.id), 'facet', `${facet.facetName}层级`); });
  return { nodes, links };
}
