import type { VisualNode } from './graphData';

export function compactGraphForce() {
  let nodes: VisualNode[] = [];
  const force = (alpha: number) => {
    const radius = Math.max(78, Math.cbrt(nodes.length) * 24);
    for (const node of nodes) {
      const x = node.x || 0, y = node.y || 0, z = node.z || 0;
      const length = Math.hypot(x, y, z) || 1;
      // A weak central pull bounds disconnected real facets without introducing semantic links.
      const strength = (0.018 + Math.max(0, length - radius) / length * 0.22) * alpha;
      if (node.fx == null) node.vx = (node.vx || 0) - x * strength;
      if (node.fy == null) node.vy = (node.vy || 0) - y * strength;
      if (node.fz == null) node.vz = (node.vz || 0) - z * strength;
    }
    // Keep spheres spatially separate. Node coordinates remain a layout, never invented relationships.
    for (let a = 0; a < nodes.length; a += 1) for (let b = a + 1; b < nodes.length; b += 1) {
      const first = nodes[a], second = nodes[b];
      const dx = (first.x || 0) - (second.x || 0) || 0.01 * ((a % 3) + 1);
      const dy = (first.y || 0) - (second.y || 0) || 0.01 * ((b % 5) + 1);
      const dz = (first.z || 0) - (second.z || 0) || 0.01;
      const distance = Math.hypot(dx, dy, dz);
      const spacing = first.id === 'central' || second.id === 'central' ? 31 : 25;
      if (distance >= spacing) continue;
      const correction = (spacing - distance) / distance * 0.23 * alpha;
      for (const [velocity, fixed, delta] of [['vx', 'fx', dx], ['vy', 'fy', dy], ['vz', 'fz', dz]] as const) {
        if (first[fixed] == null) first[velocity] = (first[velocity] || 0) + delta * correction;
        if (second[fixed] == null) second[velocity] = (second[velocity] || 0) - delta * correction;
      }
    }
  };
  force.initialize = (value: VisualNode[]) => { nodes = value; };
  return force;
}

export function keyGraphNodeIds(nodes: VisualNode[]): Set<string> {
  const root = nodes.find(node => node.id === 'central') || nodes.find(node => node.sourceKind === 'agent' && !node.parentId);
  const children = nodes.filter(node => node.active && node.parentId === root?.id).sort((a, b) => Number(b.status === 'running') - Number(a.status === 'running')).slice(0, 3);
  return new Set([...(root ? [root.id] : []), ...children.map(node => node.id)]);
}
