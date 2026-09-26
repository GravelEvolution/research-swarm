import type { Snapshot } from './types';

export type TaskPhase = 'empty' | 'requirements' | 'retrieving' | 'researching' | 'completed' | 'failed';
export interface TaskSummary { id: string; title: string; phase: TaskPhase; updatedAt: string; round: number }
export interface ResearchDocument { markdown: string; revision: number; polishing: boolean; polishedFrom?: number | null; source: 'model' | 'local'; questions: string[]; error: string | null }
export interface Message { id: string; role: 'user' | 'assistant' | 'system'; content: string; at: string; kind?: 'requirements' | 'progress' | 'result' }
export interface RunSummary { round: number; at: string; summary: string; mode: string }
export interface TaskDetail {
  task: TaskSummary;
  phase: TaskPhase;
  document: ResearchDocument;
  messages: Message[];
  state: Snapshot | null;
  error: string | null;
  artifacts: { name: string; kind: string; url: string }[];
  runs: RunSummary[];
  modelReady?: boolean;
}
export const taskPhaseLabels: Record<TaskPhase, string> = { empty: '新任务', requirements: '准备研究', retrieving: '检索资料', researching: '研究中', completed: '已完成', failed: '需要处理' };
