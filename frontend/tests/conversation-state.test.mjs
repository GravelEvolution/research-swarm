import assert from 'node:assert/strict';
import test from 'node:test';
import { isModelReady, preparationReply } from '../src/conversationState.ts';

test('current task readiness replaces stale settings and settings serve unselected tasks', () => {
  assert.equal(isModelReady(true, false), true);
  assert.equal(isModelReady(false, true), false);
  assert.equal(isModelReady(undefined, true), true);
  assert.equal(isModelReady(undefined, undefined), false);
});

test('preparation shows only the latest guidance for the current user message', () => {
  const messages = [{ role: 'user', content: '旧问题' }, { role: 'assistant', kind: 'requirements', content: '旧摘要' }, { role: 'user', content: '新问题' }];
  assert.equal(preparationReply(messages), undefined);
  messages.push({ role: 'assistant', kind: 'requirements', content: '第一版需求摘要' }, { role: 'assistant', kind: 'requirements', content: '润色后的最新引导' }, { role: 'system', kind: 'progress', content: '已保存' });
  assert.equal(preparationReply(messages).content, '润色后的最新引导');
});
