import assert from 'node:assert/strict';
import test from 'node:test';
import { PerspectiveCamera, Vector3 } from 'three';
import { graphCameraFrame, graphNodeRadius } from '../src/graphCamera.ts';

const nodes = Array.from({ length: 39 }, (_, index) => ({ id: index ? `node-${index}` : 'central', active: index % 5 === 0, x: 260 + Math.sin(index * 2.4) * 90, y: -140 + Math.cos(index * 1.7) * 62, z: 80 + Math.sin(index * 0.8) * 55 }));

test('camera frames every actual node on both a desktop canvas and a short embedded canvas', () => {
  for (const [width, height] of [[1096, 330], [460, 143], [280, 220]]) {
    const frame = graphCameraFrame(nodes, width, height, 50);
    const camera = new PerspectiveCamera(50, width / height, 0.1, 10000);
    camera.position.set(frame.position.x, frame.position.y, frame.position.z);
    camera.lookAt(frame.target.x, frame.target.y, frame.target.z);
    camera.updateMatrixWorld();
    const projected = nodes.map(node => new Vector3(node.x, node.y, node.z).project(camera));
    projected.forEach(point => { assert.ok(Math.abs(point.x) < 0.93); assert.ok(Math.abs(point.y) < 0.93); assert.ok(point.z > -1 && point.z < 1); });
    const heightUsed = Math.max(...projected.map(point => point.y)) - Math.min(...projected.map(point => point.y));
    assert.ok(heightUsed > 0.7, `Graph should use substantial canvas height, received ${heightUsed}`);
    const central = new Vector3(nodes[0].x, nodes[0].y, nodes[0].z).applyMatrix4(camera.matrixWorldInverse);
    const centralDiameterPx = graphNodeRadius(nodes[0]) * height / (Math.abs(central.z) * Math.tan(25 * Math.PI / 180));
    assert.ok(centralDiameterPx > 9, `Central node should remain a usable visible target, received ${centralDiameterPx}px`);
  }
});

test('empty or unpositioned nodes do not move the camera and one node remains visible', () => {
  assert.equal(graphCameraFrame([], 600, 200), null);
  assert.equal(graphCameraFrame([{ id: 'missing', x: NaN }], 600, 200), null);
  const frame = graphCameraFrame([{ id: 'central', active: true, x: 500, y: 20, z: -900 }], 600, 120);
  assert.deepEqual(frame.target, { x: 500, y: 20, z: -900 });
  assert.ok(Math.hypot(frame.position.x - 500, frame.position.y - 20, frame.position.z + 900) < 100);
});
