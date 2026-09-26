import assert from 'node:assert/strict';
import test from 'node:test';
import { compactGraphForce, keyGraphNodeIds } from '../src/graphLayout.ts';
import { readableNodeRadius } from '../src/graphCamera.ts';

test('disconnected outliers are gently drawn into range without new links or moving pinned nodes', () => {
  const nodes = [{ id: 'central', x: 0, y: 0, z: 0 }, { id: 'disconnected-facet', x: 900, y: 600, z: -700 }, { id: 'dragged', x: 370, y: 80, z: 90, fx: 370, fy: 80, fz: 90 }];
  const force = compactGraphForce(); force.initialize(nodes);
  for (let tick = 0; tick < 140; tick += 1) {
    force(Math.pow(0.977, tick));
    for (const node of nodes) for (const [position, velocity, fixed] of [['x', 'vx', 'fx'], ['y', 'vy', 'fy'], ['z', 'vz', 'fz']]) {
      if (node[fixed] != null) continue;
      node[velocity] = (node[velocity] || 0) * 0.62;
      node[position] += node[velocity];
    }
  }
  assert.ok(Math.hypot(nodes[1].x, nodes[1].y, nodes[1].z) < 95);
  assert.deepEqual([nodes[2].x, nodes[2].y, nodes[2].z], [370, 80, 90]);
  assert.deepEqual(nodes.map(node => node.id), ['central', 'disconnected-facet', 'dragged']);
});

test('visible sphere radius reaches at least 6.5 CSS pixels at normal fitted camera distances', () => {
  for (const [depth, height] of [[260, 330], [580, 330], [280, 143], [440, 200]]) {
    const node = { id: 'facet-1', active: false };
    const radius = readableNodeRadius(node, depth, height, 50);
    const radiusPx = radius * height / (2 * depth * Math.tan(25 * Math.PI / 180));
    assert.ok(radiusPx >= 6.49, `Target should stay selectable, received ${radiusPx}px`);
  }
  assert.ok(readableNodeRadius({ id: 'facet-1', active: false }, 1e6, 143) < 30, 'Deliberately zooming far out must not produce unbounded giant world geometry');
});

test('persistent labels select only the central agent and at most three active direct children', () => {
  const nodes = [{ id: 'central', parentId: null, sourceKind: 'agent', active: true }, ...Array.from({ length: 5 }, (_, index) => ({ id: `child-${index}`, parentId: 'central', active: true, status: index === 4 ? 'running' : 'pending' })), { id: 'grandchild', parentId: 'child-1', active: true, status: 'running' }, { id: 'facet', sourceKind: 'facet', active: false }];
  const labels = keyGraphNodeIds(nodes);
  assert.equal(labels.size, 4);
  assert.ok(labels.has('central'));
  assert.ok(labels.has('child-4'));
  assert.ok(!labels.has('grandchild'));
  assert.ok(!labels.has('facet'));
});
