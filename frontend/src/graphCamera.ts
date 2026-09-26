import type { VisualNode } from './graphData';

export interface Point3 { x: number; y: number; z: number }
export const graphNodeValue = (node: VisualNode) => node.id === 'central' ? 4.5 : node.active ? 1.7 : 1;
export const graphNodeRadius = (node: VisualNode) => 7 * Math.cbrt(graphNodeValue(node));

export function readableNodeRadius(node: VisualNode, depth: number, height: number, fov = 50): number {
  const worldPerPixel = 2 * Math.max(1, depth) * Math.tan(fov * Math.PI / 360) / Math.max(1, height);
  const targetPixels = node.id === 'central' ? 10 : node.active ? 8 : 6.5;
  const base = graphNodeRadius(node);
  return Math.max(base, Math.min(base * 3.5, targetPixels * worldPerPixel));
}

export function graphCameraFrame(nodes: VisualNode[], width: number, height: number, fov = 50): { position: Point3; target: Point3 } | null {
  const positioned = nodes.filter(node => Number.isFinite(node.x) && Number.isFinite(node.y) && Number.isFinite(node.z));
  if (!positioned.length || width < 1 || height < 1) return null;
  const bounds = (axis: 'x' | 'y' | 'z') => [Math.min(...positioned.map(node => node[axis]!)), Math.max(...positioned.map(node => node[axis]!))];
  const target = Object.fromEntries((['x', 'y', 'z'] as const).map(axis => { const [min, max] = bounds(axis); return [axis, (min + max) / 2]; })) as unknown as Point3;
  const length = Math.hypot(0.25, 0.16, 1);
  const view = { x: 0.25 / length, y: 0.16 / length, z: 1 / length };
  const rightLength = Math.hypot(view.z, view.x);
  const right = { x: view.z / rightLength, y: 0, z: -view.x / rightLength };
  const up = { x: view.y * right.z, y: view.z * right.x - view.x * right.z, z: -view.y * right.x };
  // Use a proportionate margin. Fixed pixel padding nearly eliminates the FOV of short canvases.
  const tanY = Math.tan(fov * Math.PI / 360) * 0.86;
  const tanX = tanY * width / height;
  let distance = 65;
  for (const node of positioned) {
    const offset = { x: node.x! - target.x, y: node.y! - target.y, z: node.z! - target.z };
    const dot = (axis: Point3) => offset.x * axis.x + offset.y * axis.y + offset.z * axis.z;
    const radius = graphNodeRadius(node);
    distance = Math.max(distance, dot(view) + radius + Math.max((Math.abs(dot(right)) + radius) / tanX, (Math.abs(dot(up)) + radius) / tanY));
  }
  return { target, position: { x: target.x + view.x * distance, y: target.y + view.y * distance, z: target.z + view.z * distance } };
}
