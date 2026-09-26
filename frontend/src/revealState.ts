export interface RevealToken { id: number; text: string }
export interface RevealState { text: string; tokens: RevealToken[]; nextId: number }

const graphemes = new Intl.Segmenter('zh-CN', { granularity: 'grapheme' });
export function splitGraphemes(text: string): string[] {
  return Array.from(graphemes.segment(text), part => part.segment);
}

/** Stable identities prevent polling and appended model output from replaying old text. */
export function reconcileReveal(previous: RevealState | undefined, text: string): RevealState {
  if (previous?.text === text) return previous;
  const old = previous?.tokens || [], incoming = splitGraphemes(text);
  let prefix = 0, suffix = 0, nextId = previous?.nextId || 0;
  while (prefix < old.length && prefix < incoming.length && old[prefix].text === incoming[prefix]) prefix++;
  while (suffix < old.length - prefix && suffix < incoming.length - prefix && old[old.length - 1 - suffix].text === incoming[incoming.length - 1 - suffix]) suffix++;
  const tokens = incoming.map((part, index) => index < prefix ? old[index]
    : index >= incoming.length - suffix ? old[old.length - incoming.length + index]
    : { id: nextId++, text: part });
  return { text, tokens, nextId };
}

export function revealTiming(index: number, reduced: boolean, kind: 'content' | 'status' = 'content') {
  return reduced ? { duration: 100, delay: 0, blur: 0, travel: 0 }
    : { duration: kind === 'status' ? 280 : 850, delay: kind === 'status' ? Math.min(index * 12, 72) : Math.min(index * 25, 240), blur: 14, travel: kind === 'status' ? 3 : 9 };
}
