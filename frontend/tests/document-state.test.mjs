import assert from 'node:assert/strict';
import test from 'node:test';
import { draftReducer, emptyDraft } from '../src/documentState.ts';

const document = (revision, markdown, source = 'model', polishedFrom = null) => ({ revision, markdown, source, polishedFrom, polishing: false, questions: [], error: null });
const start = () => draftReducer(emptyDraft(), { type: 'reset', taskId: 'task-a', document: document(1, '# 原始需求') });

test('polling never replaces unsaved user input with a newer model document', () => {
  let state = draftReducer(start(), { type: 'edit', text: '# 用户正在输入' });
  state = draftReducer(state, { type: 'server', document: document(2, '# 异步模型结果') });
  assert.equal(state.text, '# 用户正在输入');
  assert.equal(state.dirty, true);
  assert.equal(state.conflict, true);
});

test('save acknowledgment preserves typing that happened while the request was in flight', () => {
  let state = draftReducer(start(), { type: 'edit', text: '# 第一段' });
  state = draftReducer(state, { type: 'saving' });
  state = draftReducer(state, { type: 'edit', text: '# 第一段\n第二段还在写' });
  state = draftReducer(state, { type: 'saved', document: document(2, '# 第一段', 'local') });
  assert.equal(state.text, '# 第一段\n第二段还在写');
  assert.equal(state.baseRevision, 2);
  assert.equal(state.dirty, true);
});

test('own polish advances the save revision without overwriting continued editing', () => {
  let state = draftReducer(start(), { type: 'edit', text: '# 第一段' });
  state = draftReducer(state, { type: 'saving' });
  state = draftReducer(state, { type: 'saved', document: document(2, '# 第一段', 'local') });
  state = draftReducer(state, { type: 'edit', text: '# 第一段\n第二段' });
  state = draftReducer(state, { type: 'server', document: document(3, '# AI 润色后的第一段', 'model', 2) });
  assert.equal(state.text, '# 第一段\n第二段');
  assert.equal(state.baseRevision, 3);
  assert.equal(state.conflict, false);
});

test('polish arriving before the save response does not erase newer editing', () => {
  let state = draftReducer(start(), { type: 'edit', text: '# 第一段' });
  state = draftReducer(state, { type: 'saving' });
  state = draftReducer(state, { type: 'edit', text: '# 第一段\n继续输入' });
  state = draftReducer(state, { type: 'server', document: document(3, '# 润色版本', 'model', 2) });
  state = draftReducer(state, { type: 'saved', document: document(2, '# 第一段', 'local') });
  assert.equal(state.text, '# 第一段\n继续输入');
  assert.equal(state.baseRevision, 3);
  assert.equal(state.dirty, true);
});

test('409 preserves text and requires an explicit conflict resolution', () => {
  let state = draftReducer(start(), { type: 'edit', text: '# 我的版本' });
  state = draftReducer(state, { type: 'saving' });
  state = draftReducer(state, { type: 'failed', error: '版本变化', conflict: true });
  state = draftReducer(state, { type: 'server', document: document(4, '# 另一客户端的版本', 'local') });
  assert.equal(state.text, '# 我的版本');
  assert.equal(state.conflict, true);
  const kept = draftReducer(state, { type: 'keep-local' });
  assert.equal(kept.text, '# 我的版本');
  assert.equal(kept.baseRevision, 4);
  assert.equal(kept.dirty, true);
  const adopted = draftReducer(state, { type: 'use-server' });
  assert.equal(adopted.text, '# 另一客户端的版本');
  assert.equal(adopted.dirty, false);
});

test('another client model polish is a conflict even after this client saved a document', () => {
  let state = draftReducer(start(), { type: 'edit', text: '# 我的已提交版本' });
  state = draftReducer(state, { type: 'saving' });
  state = draftReducer(state, { type: 'saved', document: document(2, '# 我的已提交版本', 'local') });
  state = draftReducer(state, { type: 'edit', text: '# 我的继续输入' });
  state = draftReducer(state, { type: 'server', document: document(4, '# 另一客户端的模型润色', 'model', 3) });
  assert.equal(state.text, '# 我的继续输入');
  assert.equal(state.baseRevision, 2);
  assert.equal(state.conflict, true);
});

test('a model document without explicit polish provenance cannot adopt the local revision', () => {
  let state = draftReducer(start(), { type: 'edit', text: '# 已提交版本' });
  state = draftReducer(state, { type: 'saving' });
  state = draftReducer(state, { type: 'saved', document: document(2, '# 已提交版本', 'local') });
  state = draftReducer(state, { type: 'edit', text: '# 未提交的新输入' });
  state = draftReducer(state, { type: 'server', document: document(3, '# 未知来源的模型版本') });
  assert.equal(state.text, '# 未提交的新输入');
  assert.equal(state.baseRevision, 2);
  assert.equal(state.conflict, true);
});

test('clean documents accept newer polish while stale polling responses are ignored', () => {
  let state = draftReducer(start(), { type: 'server', document: document(3, '# 最新润色') });
  state = draftReducer(state, { type: 'server', document: document(2, '# 旧轮询') });
  assert.equal(state.text, '# 最新润色');
  assert.equal(state.baseRevision, 3);
});

test('switching tasks resets draft ownership and pending save metadata', () => {
  let state = draftReducer(start(), { type: 'edit', text: '# 任务 A 草稿' });
  state = draftReducer(state, { type: 'saving' });
  state = draftReducer(state, { type: 'reset', taskId: 'task-b', document: document(1, '# 任务 B') });
  assert.equal(state.taskId, 'task-b');
  assert.equal(state.text, '# 任务 B');
  assert.equal(state.saving, false);
  assert.equal(state.submitted, null);
});
