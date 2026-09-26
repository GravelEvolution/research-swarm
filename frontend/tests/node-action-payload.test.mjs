import assert from 'node:assert/strict';
import test from 'node:test';
import { isNodeImpactCurrent, nodeActionFingerprint, nodeActionRequest } from '../src/nodeActionPayload.ts';

const draft = { nodeId: 'node-a', kind: 'modify', text: '  新的需求  ', query: '', allowNewSearch: false };
const review = value => ({ impact: { revision: 42, affectedIds: ['node-a', 'central'], downstreamCount: 1 }, fingerprint: nodeActionFingerprint(value) });

test('a node mutation requires a reviewed matching action and the current state revision', () => {
  assert.throws(() => nodeActionRequest(draft, null, 42), /预览/);
  assert.throws(() => nodeActionRequest(draft, review(draft), 43), /预览/);
  assert.equal(isNodeImpactCurrent(review(draft), { ...draft, text: '已改写的需求' }, 42), false);
  assert.equal(isNodeImpactCurrent(review(draft), { ...draft, kind: 'reject' }, 42), false);
});

test('modify insert and reject send the reviewed revision without an empty acceptance field', () => {
  for (const kind of ['modify', 'insert', 'reject']) {
    const value = { ...draft, kind };
    const request = nodeActionRequest(value, review(value), 42);
    assert.equal(request.path, '/actions/intervene');
    assert.deepEqual(request.body, { nodeId: 'node-a', kind, text: '新的需求', expectedRevision: 42 });
    assert.ok(!Object.hasOwn(request.body, 'acceptance'));
  }
});

test('deepening applies only the query and search permission explicitly included in its preview', () => {
  const value = { ...draft, kind: 'deepen', query: ' efficient inference ', allowNewSearch: true };
  const request = nodeActionRequest(value, review(value), 42);
  assert.equal(request.path, '/deepen');
  assert.equal(request.body.query, 'efficient inference');
  assert.equal(request.body.allowNewSearch, true);
  assert.equal(request.body.expectedRevision, 42);
  assert.equal(isNodeImpactCurrent(review(value), { ...value, allowNewSearch: false }, 42), false);
});
