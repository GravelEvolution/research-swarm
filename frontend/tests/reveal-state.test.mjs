import assert from 'node:assert/strict';
import test from 'node:test';
import { reconcileReveal, revealTiming, splitGraphemes } from '../src/revealState.ts';

test('Chinese, joined emoji and combining accents remain whole during entry', () => {
  assert.deepEqual(splitGraphemes('研究👩🏽‍💻e\u0301'), ['研', '究', '👩🏽‍💻', 'e\u0301']);
});

test('unchanged polling keeps every character identity and does not enter again', () => {
  const first = reconcileReveal(undefined, '正在检索论文');
  const poll = reconcileReveal(first, '正在检索论文');
  assert.equal(poll, first);
});

test('incremental text preserves already displayed prefix and unchanged suffix', () => {
  const before = reconcileReveal(undefined, '结果：支持。');
  const after = reconcileReveal(before, '结果：部分支持。');
  assert.deepEqual(after.tokens.slice(0, 3), before.tokens.slice(0, 3));
  assert.deepEqual(after.tokens.slice(-3), before.tokens.slice(-3));
  assert.ok(after.tokens.slice(3, 5).every(token => token.id >= before.nextId));
  const appended = reconcileReveal(after, '结果：部分支持。需验证');
  assert.deepEqual(appended.tokens.slice(0, after.tokens.length), after.tokens);
});

test('rapid status replacements never reuse removed character identities', () => {
  let value = reconcileReveal(undefined, '运行中');
  const oldIds = new Set(value.tokens.map(token => token.id));
  value = reconcileReveal(value, '');
  value = reconcileReveal(value, '已完成');
  assert.ok(value.tokens.every(token => !oldIds.has(token.id)));
});

test('long generated content finishes its stagger promptly, reduced motion removes travel and blur', () => {
  const normal = revealTiming(3000, false, 'content');
  assert.ok(normal.delay <= 240);
  assert.equal(normal.blur, 14);
  assert.equal(revealTiming(0, false, 'content').duration, 850);
  assert.ok(revealTiming(20, false, 'status').duration <= 300);
  const reduced = revealTiming(100, true, 'content');
  assert.equal(reduced.delay, 0);
  assert.equal(reduced.blur, 0);
  assert.equal(reduced.travel, 0);
  assert.ok(reduced.duration <= 120);
});
