import assert from 'node:assert/strict';
import test from 'node:test';
import { buildGraphData, mergeGraphNodes, mergeGraphLinkReasons } from '../src/graphData.ts';

test('polling refreshes relationship evidence without replacing simulated endpoints', () => {
  const source = { id: 'central', x: 23, fx: 23 }, target = { id: 'child', x: 46 };
  const link = { source, target, type: 'decompose', reason: '旧判断' };
  mergeGraphLinkReasons([link], [{ source: 'central', target: 'child', type: 'decompose', reason: '新证据要求追加对照' }]);
  assert.equal(link.reason, '新证据要求追加对照');
  assert.equal(link.source, source);
  assert.equal(link.target, target);
  assert.equal(source.fx, 23);
});

test('3D graph contains actual agents, missing facet nodes and their hierarchy without invented nodes', () => {
  const graph = buildGraphData({
    nodes: [{ id: 'central', title: '总 agent', status: 'running', phase: 'plan', logs: [{ message: '检索中' }], sourceNodeId: null, active: true, parentId: null }, { id: 'agent-1', title: '方法分析', status: 'pending', phase: 'execute', logs: [], sourceNodeId: '1', active: false, parentId: 'central' }],
    edges: [{ source: 'central', target: 'agent-1', type: 'decompose', reason: '研究分工' }],
    facetNodes: [{ id: '1', title: '方法', parentId: null, facetName: '方法树', paperIds: ['p1'] }, { id: '2', title: '子方法', parentId: '1', facetName: '方法树', paperIds: ['p2'] }],
  });
  assert.deepEqual(graph.nodes.map(node => node.id), ['central', 'agent-1', 'facet:2']);
  assert.equal(graph.nodes[0].action, '检索中');
  assert.ok(graph.links.some(link => link.source === 'agent-1' && link.target === 'facet:2' && link.type === 'facet'));
  assert.equal(graph.links.filter(link => link.source === 'central' && link.target === 'agent-1').length, 1);
});

test('an empty task never renders decorative or demo graph nodes', () => {
  assert.deepEqual(buildGraphData(null), { nodes: [], links: [] });
});

test('polling changes status while keeping simulated and manually dragged node positions', () => {
  const oldNode = { id: 'agent-1', title: '方法分析', status: 'pending', x: 65, y: -12, z: 37, fx: 65, fy: -12, fz: 37 };
  const incoming = [{ id: 'agent-1', title: '方法分析', status: 'completed', action: '已有可追溯输出' }, { id: 'agent-2', title: '新分支', status: 'pending' }];
  const merged = mergeGraphNodes(incoming, new Map([[oldNode.id, oldNode]]));
  assert.equal(merged[0], oldNode);
  assert.equal(merged[0].status, 'completed');
  assert.deepEqual([merged[0].x, merged[0].y, merged[0].z, merged[0].fx, merged[0].fy, merged[0].fz], [65, -12, 37, 65, -12, 37]);
  assert.equal(merged[1].id, 'agent-2');
});
