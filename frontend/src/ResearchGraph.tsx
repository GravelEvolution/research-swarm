import { useEffect, useMemo, useRef, useState } from 'react';
import { Alert, Button, Drawer, Input, Tag, Tooltip } from 'antd';
import { ExpandOutlined, UnorderedListOutlined, SearchOutlined } from '@ant-design/icons';
import ForceGraph3D, { type ForceGraph3DInstance } from '3d-force-graph';
import { AmbientLight, DirectionalLight, type PerspectiveCamera } from 'three';
import { buildGraphData, mergeGraphLinkReasons, mergeGraphNodes, type VisualLink, type VisualNode } from './graphData';
import { graphCameraFrame, graphNodeValue } from './graphCamera';
import { compactGraphForce, keyGraphNodeIds } from './graphLayout';
import { createNodeVisual, disposeNodeVisual, updateNodeVisual, type NodeVisual } from './graphVisuals';
import { BlurText, revealElement } from './BlurReveal';
import type { Snapshot } from './types';

const states = { pending: '待执行', running: '运行中', completed: '已完成', failed: '执行失败', waiting_user: '待处理' };
const color = (node: VisualNode) => ({ running: '#447fbd', completed: '#7caa9d', failed: '#bf7771', waiting_user: '#b4a175', pending: node.active ? '#9aadc4' : '#cbd3df' })[node.status];

export default function ResearchGraph({ state, onSelect, retrieving = false }: { state: Snapshot | null; onSelect: (node: VisualNode) => void; retrieving?: boolean }) {
  const container = useRef<HTMLDivElement>(null);
  const graph = useRef<ForceGraph3DInstance<VisualNode, VisualLink> | null>(null);
  const existing = useRef(new Map<string, VisualNode>());
  const structure = useRef('');
  const userMoved = useRef(false);
  const fitPending = useRef(true);
  const fitView = useRef<() => boolean>(() => false);
  const select = useRef(onSelect);
  const hovered = useRef<{ id: string; name: HTMLElement; status: HTMLElement; action: HTMLElement } | null>(null);
  select.current = onSelect;
  const [error, setError] = useState('');
  const [listOpen, setListOpen] = useState(false);
  const [search, setSearch] = useState('');
  const data = useMemo(() => buildGraphData(state), [state]);
  const latest = useRef(data);
  latest.current = data;

  useEffect(() => {
    if (!container.current) return;
    const host = container.current;
    let observer: ResizeObserver | null = null;
    let instance: ForceGraph3DInstance<VisualNode, VisualLink> | null = null;
    const visuals = new Map<string, NodeVisual>();
    const media = window.matchMedia('(prefers-reduced-motion: reduce)');
    let viewportHeight = 1;
    const markInteraction = () => { userMoved.current = true; };
    host.addEventListener('pointerdown', markInteraction);
    host.addEventListener('wheel', markInteraction, { passive: true });
    try {
      // The package constructor defaults to its base node type; this instance only receives our typed graph data below.
      instance = new ForceGraph3D(host, { controlType: 'orbit', rendererConfig: { antialias: true, alpha: true, powerPreference: 'high-performance' } }) as unknown as ForceGraph3DInstance<VisualNode, VisualLink>;
      graph.current = instance;
      instance.backgroundColor('#fcfcfd').showNavInfo(false).numDimensions(3).nodeId('id').nodeVal(graphNodeValue).nodeRelSize(7).nodeResolution(16).nodeOpacity(1).nodeColor(color).linkColor(link => link.type === 'facet' ? '#9eafc4' : '#718aa9').linkOpacity(0.7).linkWidth(link => link.type === 'facet' ? 0.65 : 0.9).enableNodeDrag(true).enableNavigationControls(true).warmupTicks(70).cooldownTicks(100).d3VelocityDecay(0.38);
      const charge = instance.d3Force('charge') as { strength: (value: number) => void } | undefined;
      const link = instance.d3Force('link') as { distance: (value: number) => void } | undefined;
      charge?.strength(-140); link?.distance(64);
      instance.d3Force('compact', compactGraphForce());
      instance.nodeThreeObject(node => { const previous = visuals.get(node.id); if (previous) { previous.node = node; return previous.group; } const visual = createNodeVisual(node, color(node)); visuals.set(node.id, visual); return visual.group; });
      instance.scene().onBeforeRender = (_renderer, _scene, camera) => {
        const ids = keyGraphNodeIds(latest.current.nodes);
        const visibleNodes = existing.current;
        for (const [id, visual] of visuals) {
          if (!visibleNodes.has(id)) { disposeNodeVisual(visual); visuals.delete(id); continue; }
          updateNodeVisual(visual, camera as PerspectiveCamera, viewportHeight, ids.has(id), color(visual.node), media.matches);
        }
      };
      instance.nodeLabel(node => {
        const tooltip = document.createElement('div'); tooltip.className = 'graph-node-tooltip';
        const name = document.createElement('strong'); name.textContent = node.title;
        const status = document.createElement('span'); status.textContent = `${node.sourceKind === 'facet' ? '研究切面' : states[node.status]}${node.active ? ' · 本轮活动' : ''}`;
        const action = document.createElement('p'); action.textContent = node.action;
        hovered.current = { id: node.id, name, status, action };
        tooltip.append(name, status, action); return tooltip;
      });
      instance.linkLabel(link => { const label = document.createElement('span'); label.textContent = link.reason || ({ decompose: '任务分解', return: '结果汇总', compare: '对比关联', facet: '切面层级' } as Record<string, string>)[link.type] || '研究关联'; return label; });
      instance.onNodeClick(node => select.current(node));
      instance.onNodeHover(node => { if (!node) hovered.current = null; });
      instance.onNodeDragEnd(node => { node.fx = node.x; node.fy = node.y; node.fz = node.z; });
      const ambient = new AmbientLight('#ffffff', 1.7); const directional = new DirectionalLight('#ffffff', 1.3); directional.position.set(100, 160, 180); instance.lights([ambient, directional]);
      instance.renderer().setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
      fitView.current = () => {
        if (!instance) return false;
        const box = host.getBoundingClientRect();
        const frame = graphCameraFrame(instance.graphData().nodes, box.width, box.height, (instance.camera() as PerspectiveCamera).fov);
        if (!frame) return false;
        instance.cameraPosition(frame.position, frame.target, 0);
        return true;
      };
      let initialFit = false;
      instance.onEngineTick(() => { if (!initialFit && !userMoved.current) initialFit = fitView.current(); });
      instance.onEngineStop(() => { if (fitPending.current && !userMoved.current && fitView.current()) fitPending.current = false; });
      const resize = () => { const box = host.getBoundingClientRect(); viewportHeight = Math.max(1, box.height); instance?.width(Math.max(1, box.width)).height(viewportHeight); if (!userMoved.current && initialFit) fitView.current(); };
      observer = new ResizeObserver(resize); observer.observe(host); resize();
      const initialNodes = latest.current.nodes.map(node => ({ ...node }));
      existing.current = new Map(initialNodes.map(node => [node.id, node]));
      instance.graphData({ nodes: initialNodes, links: latest.current.links.map(link => ({ ...link })) });
      structure.current = latest.current.nodes.map(node => node.id).join('|') + latest.current.links.map(link => `${String(link.source)}>${String(link.target)}:${link.type}`).join('|');
    } catch (error) { setError(error instanceof Error ? error.message : '当前环境无法创建 WebGL 图。'); }
    return () => { observer?.disconnect(); host.removeEventListener('pointerdown', markInteraction); host.removeEventListener('wheel', markInteraction); instance?._destructor(); visuals.forEach(disposeNodeVisual); visuals.clear(); graph.current = null; existing.current.clear(); structure.current = ''; fitView.current = () => false; userMoved.current = false; fitPending.current = true; host.replaceChildren(); };
  }, []);

  useEffect(() => {
    const instance = graph.current;
    if (!instance) return;
    const nodes = mergeGraphNodes(data.nodes, existing.current);
    existing.current = new Map(nodes.map(node => [node.id, node]));
    const key = data.nodes.map(node => node.id).join('|') + data.links.map(link => `${String(link.source)}>${String(link.target)}:${link.type}`).join('|');
    if (key !== structure.current) { structure.current = key; fitPending.current = true; instance.graphData({ nodes, links: data.links.map(link => ({ ...link })) }); }
    else mergeGraphLinkReasons(instance.graphData().links, data.links);
    const tooltip = hovered.current;
    const current = tooltip && existing.current.get(tooltip.id);
    if (tooltip && current && tooltip.name.isConnected) {
      const update = (element: HTMLElement, value: string) => { if (element.textContent !== value) { element.textContent = value; revealElement(element, 'status'); } };
      update(tooltip.name, current.title);
      update(tooltip.status, `${current.sourceKind === 'facet' ? '研究切面' : states[current.status]}${current.active ? ' · 本轮活动' : ''}`);
      update(tooltip.action, current.action);
    }
    // Status is updated by the local visual renderer; unchanged polls never rebuild nodes.
  }, [data]);

  const matches = data.nodes.filter(node => `${node.title} ${node.action}`.toLowerCase().includes(search.trim().toLowerCase()));
  return <section className="research-graph" aria-label="三维科研结构">
    <div className="graph-toolbar"><div><strong>研究结构</strong><span><BlurText kind="status" text={`${data.nodes.length} 个节点 · ${data.links.length} 条关联`} /></span></div><div className="graph-actions"><Tooltip title="重置视角"><Button type="text" icon={<ExpandOutlined />} aria-label="重置三维图视角" onClick={() => { fitView.current(); userMoved.current = true; }} /></Tooltip><Button type="text" icon={<UnorderedListOutlined />} onClick={() => setListOpen(true)}>节点列表</Button></div></div>
    <div ref={container} className="graph-canvas" role="img" aria-label="可旋转、缩放和拖动节点的三维科研结构图。使用节点列表可通过键盘访问每个节点。" />
    {!data.nodes.length && !error && <div className="graph-empty"><span><BlurText text={retrieving ? '正在检索并形成研究结构' : '等待研究节点'} /></span><p>真实论文和任务就绪后显示节点关系。</p></div>}
    {error && <div className="graph-failure"><Alert type="warning" title="当前环境无法显示 3D 图。研究仍在继续，可使用节点列表检查所有任务。" /><Button onClick={() => setListOpen(true)}>打开节点列表</Button><details><summary>查看渲染错误</summary>{error}</details></div>}
    <div className="graph-footer"><div className="graph-legend"><span><i style={{ background: '#447fbd' }} />运行</span><span><i style={{ background: '#7caa9d' }} />完成</span><span><i style={{ background: '#cbd3df' }} />待执行 / 切面</span><span><i style={{ background: '#bf7771' }} />失败</span></div><span>拖动空白旋转 · 滚轮缩放 · 拖动节点调整位置</span></div>
    <Drawer open={listOpen} title="研究节点" size={470} onClose={() => setListOpen(false)} className="task-drawer"><Input prefix={<SearchOutlined />} value={search} onChange={event => setSearch(event.target.value)} allowClear placeholder="搜索节点或当前动作" aria-label="搜索研究节点" /><div className="accessible-node-list">{matches.map(node => <button key={node.id} onClick={() => { select.current(node); setListOpen(false); }}><div><strong>{node.title}</strong><Tag color={node.status === 'running' ? 'blue' : node.status === 'failed' ? 'red' : 'default'}><BlurText kind="status" text={node.sourceKind === 'facet' ? '研究切面' : states[node.status]} /></Tag></div><p><BlurText text={node.action} /></p></button>)}{!matches.length && <p className="quiet-text">没有符合条件的节点。</p>}</div></Drawer>
  </section>;
}
