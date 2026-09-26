import { memo, useLayoutEffect, useMemo, useRef, useState, type ReactNode, type RefObject } from 'react';
import { reconcileReveal, revealTiming, type RevealState, type RevealToken } from './revealState';
import './motion.css';

const ease = 'cubic-bezier(0.23, 1, 0.32, 1)';
let motionMedia: MediaQueryList | undefined;
const activeAnimations = new Set<Animation>();
function motionPreference() {
  if (!motionMedia) {
    motionMedia = window.matchMedia('(prefers-reduced-motion: reduce)');
    motionMedia.addEventListener('change', () => { if (motionMedia?.matches) activeAnimations.forEach(animation => animation.cancel()); });
  }
  return motionMedia;
}
export const prefersReducedMotion = () => typeof window !== 'undefined' && motionPreference().matches;

const visibilityCallbacks = new Map<Element, () => void>();
let visibilityObserver: IntersectionObserver | undefined;
function useRevealVisibility(ref: RefObject<HTMLElement>) {
  const [visible, setVisible] = useState(false);
  useLayoutEffect(() => {
    const element = ref.current;
    if (!element) return;
    const box = element.getBoundingClientRect();
    if (box.width && box.height && box.bottom > 0 && box.top < window.innerHeight && box.right > 0 && box.left < window.innerWidth) { setVisible(true); return; }
    visibilityObserver ||= new IntersectionObserver(entries => {
      for (const entry of entries) if (entry.isIntersecting && entry.intersectionRect.width && entry.intersectionRect.height) {
        visibilityCallbacks.get(entry.target)?.(); visibilityCallbacks.delete(entry.target); visibilityObserver?.unobserve(entry.target);
      }
    });
    visibilityCallbacks.set(element, () => setVisible(true)); visibilityObserver.observe(element);
    return () => { visibilityObserver?.unobserve(element); visibilityCallbacks.delete(element); };
  }, []);
  return visible;
}

export function revealElement(element: HTMLElement, kind: 'content' | 'status' = 'content', index = 0): Animation | undefined {
  if (!element.animate) return;
  const reduced = prefersReducedMotion(), timing = revealTiming(index, reduced, kind);
  const animation = element.animate([
    { opacity: reduced ? 0.75 : 0, filter: `blur(${timing.blur}px)`, transform: reduced ? 'none' : `translateY(${timing.travel}px) rotateX(-12deg)` },
    { opacity: 1, filter: 'blur(0px)', transform: 'none' },
  ], { duration: timing.duration, delay: timing.delay, easing: ease, fill: 'backwards' });
  activeAnimations.add(animation);
  const release = () => { activeAnimations.delete(animation); animation.removeEventListener('finish', release); animation.removeEventListener('cancel', release); };
  animation.addEventListener('finish', release); animation.addEventListener('cancel', release);
  return animation;
}

function Glyph({ token, index, kind, visible }: { token: RevealToken; index: number; kind: 'content' | 'status'; visible: boolean }) {
  const ref = useRef<HTMLSpanElement>(null);
  useLayoutEffect(() => {
    if (!visible || !ref.current || !token.text.trim()) return;
    let animation = revealElement(ref.current, kind, index);
    const settle = () => { animation = undefined; };
    animation?.addEventListener('finish', settle, { once: true });
    animation?.addEventListener('cancel', settle, { once: true });
    return () => { animation?.cancel(); animation = undefined; };
  }, [visible]);
  // Line breaks must remain in the outer inline formatting context (especially code blocks).
  return token.text.trim() ? <span ref={ref} className="blur-glyph">{token.text}</span> : <>{token.text}</>;
}

/** Only new/replaced graphemes mount; existing characters keep their animation and identity. */
export const BlurText = memo(function BlurText({ text, kind = 'content', className = '' }: { text: string; kind?: 'content' | 'status'; className?: string }) {
  const ref = useRef<HTMLSpanElement>(null);
  const visible = useRevealVisibility(ref);
  const previous = useRef<RevealState>();
  const value = useMemo(() => reconcileReveal(previous.current, text), [text]);
  const firstNew = previous.current?.nextId || 0;
  useLayoutEffect(() => { previous.current = value; }, [value]);
  const groups: RevealToken[][] = [];
  // Keep Latin words intact when wrapping; Chinese and emoji still wrap by grapheme.
  for (const token of value.tokens) {
    const word = /^[\p{Script=Latin}\p{N}\p{M}_/.:@%+#=-]+$/u.test(token.text);
    const tail = groups.at(-1);
    if (word && tail && /^[\p{Script=Latin}\p{N}\p{M}_/.:@%+#=-]+$/u.test(tail[0].text)) tail.push(token);
    else groups.push([token]);
  }
  // Long paragraphs enter as a block; their content is still blurred, without thousands of layers.
  const long = value.tokens.length > 240;
  return <span ref={ref} className={`blur-text ${className}`} data-blur-owned="true"><span className="motion-readable">{text}</span><span aria-hidden="true" style={{ visibility: visible ? undefined : 'hidden' }}>{long ? <LongReveal text={text} kind={kind} visible={visible} /> : groups.map(group => <span className={group.length > 1 ? 'blur-word' : 'blur-unit'} key={Math.min(...group.map(token => token.id))}>{group.map(token => <Glyph key={token.id} token={token} visible={visible} kind={kind} index={Math.max(0, token.id - firstNew)} />)}</span>)}</span></span>;
});

function LongReveal({ text, kind, visible }: { text: string; kind: 'content' | 'status'; visible: boolean }) {
  const ref = useRef<HTMLSpanElement>(null);
  useLayoutEffect(() => { if (!visible || !ref.current) return; const animation = revealElement(ref.current, kind); return () => animation?.cancel(); }, [text, visible, kind]);
  return <span ref={ref} className="blur-long">{text}</span>;
}

export function BlurChange({ value, children, className = '', as: Tag = 'div' }: { value: string | number; children: ReactNode; className?: string; as?: 'div' | 'span' }) {
  const ref = useRef<HTMLDivElement & HTMLSpanElement>(null);
  useLayoutEffect(() => {
    if (!ref.current || ref.current.contains(document.activeElement)) return;
    const animation = revealElement(ref.current, 'status');
    return () => animation?.cancel();
  }, [value]);
  return <Tag ref={ref} className={className} data-blur-owned="true">{children}</Tag>;
}

const excluded = '[data-blur-owned], [data-motion-skip], textarea, input, select, option, script, style, svg, canvas, [contenteditable="true"], .graph-canvas';
const surfaces = '.ant-modal-container, .ant-modal-content, .ant-drawer-content, .ant-message-notice-content, .ant-notification-notice, .ant-dropdown-menu, .ant-tooltip-inner, .ant-popover-inner';

/** Animate native AntD feedback and all unwrapped live text, including portals. Never rewrite React's DOM. */
export function useLiveMotion() {
  useLayoutEffect(() => {
    const running = new Map<HTMLElement, Animation>();
    const signatures = new WeakMap<HTMLElement, string>();
    const pending = new Map<HTMLElement, 'content' | 'status'>();
    let frame = 0;
    const play = (element: HTMLElement, kind: 'content' | 'status', index: number) => {
      const focused = document.activeElement;
      const editing = focused instanceof HTMLElement && focused.matches('input,textarea,[contenteditable="true"]');
      if (!element.isConnected || element.closest(excluded) || element.querySelector('[data-blur-owned]') || (editing && element.contains(focused))) return;
      const text = element.textContent?.trim() || '';
      const signature = text + (element.getAttribute('aria-busy') || '') + (element.getAttribute('aria-valuenow') || '');
      if (!text && !element.matches(surfaces)) return;
      if (signatures.get(element) === signature) return;
      signatures.set(element, signature);
      running.get(element)?.cancel();
      const animation = revealElement(element, kind, Math.min(index, 8));
      if (animation) {
        running.set(element, animation);
        animation.onfinish = () => { if (running.get(element) === animation) running.delete(element); };
      }
    };
    const flush = () => {
      frame = 0;
      for (const [element, animation] of running) if (!element.isConnected) { animation.cancel(); running.delete(element); }
      const entries = [...pending]; pending.clear();
      const panels = entries.filter(([element]) => element.matches(surfaces) && !element.querySelector('[data-blur-owned]') && !element.contains(document.activeElement)).map(([element]) => element);
      let index = 0;
      for (const [element, kind] of entries) {
        if (panels.some(parent => parent !== element && parent.contains(element))) continue;
        play(element, kind, index++);
      }
    };
    const queue = (element: HTMLElement, kind: 'content' | 'status') => {
      if (element.closest(excluded)) return;
      if (!pending.has(element) || kind === 'status') pending.set(element, kind);
      if (!frame) frame = requestAnimationFrame(flush);
    };
    const collect = (node: Node) => {
      if (node.nodeType === Node.TEXT_NODE) { const parent = node.parentElement; if (parent && node.textContent?.trim()) queue(parent, 'content'); return; }
      if (!(node instanceof HTMLElement) || node.closest(excluded)) return;
      if (node.matches(surfaces) && !node.querySelector('[data-blur-owned]')) { queue(node, 'content'); return; }
      for (const child of node.childNodes) collect(child);
    };
    collect(document.getElementById('root')!);
    const observer = new MutationObserver(records => {
      for (const record of records) {
        if (record.type === 'characterData') { const parent = record.target.parentElement; if (parent) queue(parent, 'status'); }
        else if (record.type === 'childList') {
          if (record.removedNodes.length && record.target instanceof HTMLElement && !record.target.querySelector('canvas,input,textarea,[data-blur-owned]') && record.target.childElementCount < 2) queue(record.target, 'status');
          record.addedNodes.forEach(collect);
        } else if (record.target instanceof HTMLElement && record.target.matches('[role="progressbar"], [aria-busy], .ant-tag, .phase-label')) queue(record.target, 'status');
      }
    });
    observer.observe(document.body, { subtree: true, childList: true, characterData: true, attributes: true, attributeFilter: ['aria-busy', 'aria-valuenow', 'class'] });
    const media = window.matchMedia('(prefers-reduced-motion: reduce)');
    const settle = () => { if (media.matches) { running.forEach(animation => animation.cancel()); running.clear(); } };
    media.addEventListener('change', settle);
    return () => { observer.disconnect(); cancelAnimationFrame(frame); pending.clear(); running.forEach(animation => animation.cancel()); media.removeEventListener('change', settle); };
  }, []);
}
