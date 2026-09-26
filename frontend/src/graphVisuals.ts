import { CanvasTexture, Group, Mesh, MeshLambertMaterial, SphereGeometry, Sprite, SpriteMaterial, Vector3, type PerspectiveCamera } from 'three';
import type { VisualNode } from './graphData';
import { readableNodeRadius } from './graphCamera';
import { splitGraphemes } from './revealState';

export interface NodeVisual { node: VisualNode; group: Group; sphere: Mesh<SphereGeometry, MeshLambertMaterial>; label: Sprite | null; soft: Sprite; signature: string; changedAt: number; duration: number; labelText: string; labelBlur: number }

let softTexture: CanvasTexture | undefined;
function softNodeTexture() {
  if (softTexture) return softTexture;
  const canvas = document.createElement('canvas'); canvas.width = 64; canvas.height = 64;
  const context = canvas.getContext('2d')!;
  const gradient = context.createRadialGradient(32, 32, 0, 32, 32, 32);
  gradient.addColorStop(0, 'rgba(255,255,255,1)'); gradient.addColorStop(0.28, 'rgba(255,255,255,.85)'); gradient.addColorStop(0.65, 'rgba(255,255,255,.2)'); gradient.addColorStop(1, 'rgba(255,255,255,0)');
  context.fillStyle = gradient; context.fillRect(0, 0, 64, 64);
  return softTexture = new CanvasTexture(canvas);
}

export function createNodeVisual(node: VisualNode, color: string): NodeVisual {
  const group = new Group();
  const sphere = new Mesh(new SphereGeometry(1, 18, 14), new MeshLambertMaterial({ color, transparent: true }));
  sphere.scale.setScalar(7);
  group.add(sphere);
  const soft = new Sprite(new SpriteMaterial({ map: softNodeTexture(), color, transparent: true, depthWrite: false }));
  soft.raycast = () => {}; group.add(soft);
  return { node, group, sphere, soft, label: null, signature: `${node.status}|${node.title}|${node.action}`, changedAt: performance.now(), duration: 850, labelText: '', labelBlur: -1 };
}

function createLabel(text: string): Sprite {
  const canvas = document.createElement('canvas');
  const parts = splitGraphemes(text);
  const display = parts.length > 16 ? `${parts.slice(0, 16).join('')}…` : text;
  const context = canvas.getContext('2d')!;
  context.font = '500 26px "Microsoft YaHei", sans-serif';
  canvas.width = Math.ceil(context.measureText(display).width) + 24; canvas.height = 44;
  context.font = '500 26px "Microsoft YaHei", sans-serif';
  context.fillStyle = 'rgba(252,252,253,0.94)'; context.fillRect(0, 0, canvas.width, canvas.height);
  context.fillStyle = '#4b607a'; context.textBaseline = 'middle'; context.fillText(display, 12, 22);
  const texture = new CanvasTexture(canvas);
  const sprite = new Sprite(new SpriteMaterial({ map: texture, transparent: true, depthTest: false, depthWrite: false }));
  sprite.center.set(0, 0.5);
  sprite.userData.aspect = canvas.width / canvas.height;
  sprite.userData.canvas = canvas;
  sprite.userData.text = display;
  sprite.renderOrder = 5;
  sprite.raycast = () => {};
  return sprite;
}

const cameraSpace = new Vector3(), right = new Vector3(), up = new Vector3();
export function updateNodeVisual(visual: NodeVisual, camera: PerspectiveCamera, height: number, labeled: boolean, color: string, reduced = false) {
  const node = visual.node;
  const signature = `${node.status}|${node.title}|${node.action}`;
  if (signature !== visual.signature) { visual.signature = signature; visual.changedAt = performance.now(); visual.duration = 280; }
  const progress = reduced ? 1 : Math.min(1, (performance.now() - visual.changedAt) / visual.duration);
  const clarity = 1 - Math.pow(1 - progress, 3);
  cameraSpace.set(node.x || 0, node.y || 0, node.z || 0).applyMatrix4(camera.matrixWorldInverse);
  const depth = Math.max(1, -cameraSpace.z);
  const worldPerPixel = 2 * depth * Math.tan(camera.fov * Math.PI / 360) / Math.max(1, height) / camera.zoom;
  const radius = readableNodeRadius(node, depth, height * camera.zoom, camera.fov);
  visual.sphere.scale.setScalar(radius);
  visual.sphere.material.color.set(color);
  visual.sphere.material.opacity = 0.12 + clarity * 0.88;
  visual.soft.visible = progress < 1;
  visual.soft.material.color.set(color);
  visual.soft.material.opacity = (1 - clarity) * 0.7;
  visual.soft.scale.setScalar(radius * (2.6 + 2 * (1 - clarity)));
  visual.sphere.updateMatrixWorld();
  const labelText = node.id === 'central' ? '总 agent' : node.title;
  if (visual.label && labelText !== visual.labelText) { visual.group.remove(visual.label); visual.label.material.map?.dispose(); visual.label.material.dispose(); visual.label = null; }
  if (labeled && !visual.label) { visual.label = createLabel(labelText); visual.labelText = labelText; visual.labelBlur = -1; visual.group.add(visual.label); }
  if (visual.label) {
    visual.label.visible = labeled && cameraSpace.z < 0;
    if (visual.label.visible) {
      const blur = Math.round((1 - clarity) * 14);
      if (blur !== visual.labelBlur) {
        const canvas = visual.label.userData.canvas as HTMLCanvasElement, context = canvas.getContext('2d')!;
        context.clearRect(0, 0, canvas.width, canvas.height);
        context.fillStyle = 'rgba(252,252,253,0.94)'; context.fillRect(0, 0, canvas.width, canvas.height);
        context.filter = `blur(${blur}px)`; context.font = '500 26px "Microsoft YaHei", sans-serif'; context.fillStyle = '#4b607a'; context.textBaseline = 'middle'; context.fillText(String(visual.label.userData.text), 12, 22); context.filter = 'none';
        visual.label.material.map!.needsUpdate = true; visual.labelBlur = blur;
      }
      visual.label.material.opacity = 0.3 + clarity * 0.7;
      const labelHeight = 18 * worldPerPixel;
      visual.label.scale.set(labelHeight * Number(visual.label.userData.aspect), labelHeight, 1);
      right.set(1, 0, 0).applyQuaternion(camera.quaternion).multiplyScalar(radius + 4 * worldPerPixel);
      up.set(0, 1, 0).applyQuaternion(camera.quaternion).multiplyScalar(6 * worldPerPixel);
      visual.label.position.copy(right.add(up));
      visual.label.updateMatrixWorld();
    }
  }
}

export function disposeNodeVisual(visual: NodeVisual) {
  visual.sphere.geometry.dispose(); visual.sphere.material.dispose(); visual.soft.material.dispose();
  if (visual.label) { visual.label.material.map?.dispose(); visual.label.material.dispose(); }
}
