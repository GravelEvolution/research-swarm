export type NodeStatus = 'pending' | 'running' | 'completed' | 'failed' | 'waiting_user';
export type Feedback = 'interested' | 'not_interested' | 'read_later' | null;
export type Page = 'workbench' | 'requirements' | 'swarm' | 'papers' | 'recommendations' | 'report';
export type ActionKind = 'modify' | 'insert' | 'reject' | 'deepen';

export interface Activity {
  id: string;
  at: string;
  actor: 'AI' | 'user' | 'system';
  message: string;
  level?: string;
  nodeId?: string;
}
export interface Requirement {
  id: string;
  description: string;
  acceptance: string;
  constraints: string;
  version: number;
  sourceNodeIds?: string[];
}
export interface Claim {
  id: string;
  text: string;
  evidenceIds: string[];
  nodeId?: string;
  status: 'candidate' | 'confirmed' | 'rejected';
  limitations?: string;
}
export interface ResearchNode {
  id: string;
  parentId: string | null;
  title: string;
  role: string;
  kind: string;
  phase: 'plan' | 'execute' | 'aggregate';
  status: NodeStatus;
  progress: number;
  input: Record<string, unknown>;
  output: { summary?: string; evidenceIds?: string[]; claims?: Claim[]; unresolved?: string[]; structured?: Record<string, unknown>; [key: string]: unknown } | null;
  logs: Activity[];
  sourceNodeId: string | null;
  requirementIds: string[];
  evidenceIds: string[];
  startedAt: string | null;
  finishedAt: string | null;
  elapsedMs: number;
  version: number;
  active: boolean;
}
export interface Paper {
  id: string;
  title: string;
  abstract: string;
  year: number | string;
  venue: string;
  doi: string;
  authors: string[] | string;
  codeUrl: string;
  pdfAvailable: boolean;
  pdfPath: string | null;
  score: number;
  scores: Record<string, number>;
  reason: string;
  reproducibility: string;
  workerStatus: NodeStatus;
  facetNodeIds: string[];
  evidenceIds: string[];
  facts: Record<string, unknown>[];
  scoreBasis: Record<string, unknown>[];
  feedback: Feedback;
}
export interface FacetNode {
  id: string;
  parentId: string | null;
  title: string;
  facetId: string;
  facetName: string;
  topology: string;
  paperIds: string[];
}
export interface Evidence {
  id: string;
  paperId: string;
  quote: string;
  locator: string;
  type: string;
  confidence: number;
  extractor: string;
}
export interface Checkpoint {
  id: string;
  type: 'requirements' | 'recommendations' | 'comparison' | 'final';
  status: 'pending' | 'confirmed' | 'superseded';
  title: string;
  summary: string;
  createdAt: string;
  resolvedAt?: string;
  revision: number;
  decision?: string;
  userNote?: string;
}
export interface Snapshot {
  revision: number;
  project: { id: string; title: string; description: string; round: number; sourcePath: string; mode: 'evidence' | 'llm' };
  stage: number;
  paused: boolean;
  status: 'idle' | 'running' | 'waiting_user' | 'failed' | 'completed';
  requirements: Requirement[];
  nodes: ResearchNode[];
  edges: { source: string; target: string; type: 'decompose' | 'return' | 'compare'; reason: string }[];
  papers: Paper[];
  facetNodes: FacetNode[];
  evidence: Evidence[];
  checkpoints: Checkpoint[];
  activeCheckpointId: string | null;
  activities: Activity[];
  report: { summary: string; claims: Claim[]; unresolved: string[]; approved: boolean };
  history: Record<string, unknown>[];
  operations?: { id: string; type: string; nodeId?: string; message: string; status: 'running' | 'completed' | 'failed'; startedAt: string; finishedAt?: string; error?: string }[];
}
export interface Impact { revision: number; affectedIds: string[]; downstreamCount: number }
export interface Settings {
  mode: 'evidence' | 'llm';
  provider: { type: string; baseUrl: string; model: string; hasKey: boolean };
  sourcePath: string;
  capabilities: Record<string, unknown>;
}
