import React, { type ReactNode } from 'react';
import { BlurText } from './BlurReveal';

function inline(text: string): ReactNode[] {
  const pattern = /(\*\*[^*]+\*\*|`[^`]+`|\[[^\]]+\]\(https?:\/\/[^\s)]+\))/g;
  return text.split(pattern).filter(Boolean).map((part, index) => {
    if (part.startsWith('**') && part.endsWith('**')) return <strong key={index}><BlurText text={part.slice(2, -2)} /></strong>;
    if (part.startsWith('`') && part.endsWith('`')) return <code key={index}><BlurText text={part.slice(1, -1)} /></code>;
    const link = part.match(/^\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)$/);
    if (link) return <a key={index} href={link[2]} target="_blank" rel="noreferrer"><BlurText text={link[1]} /></a>;
    return <BlurText key={index} text={part} />;
  });
}

export const Markdown = React.memo(function Markdown({ text, className = '' }: { text: string; className?: string }) {
  const lines = text.replace(/\r\n/g, '\n').split('\n');
  const blocks: ReactNode[] = [];
  let index = 0;
  const cells = (line: string) => line.replace(/^\s*\||\|\s*$/g, '').split('|').map(cell => cell.trim());
  while (index < lines.length) {
    const blockKey = index;
    const line = lines[index];
    if (!line.trim()) { index += 1; continue; }
    if (/^\s*```/.test(line)) { const code: string[] = []; index += 1; while (index < lines.length && !/^\s*```/.test(lines[index])) code.push(lines[index++]); index += 1; blocks.push(<pre key={blockKey}><code><BlurText text={code.join('\n')} /></code></pre>); continue; }
    const heading = line.match(/^(#{1,6})\s+(.+)$/);
    if (heading) { blocks.push(React.createElement(`h${Math.min(heading[1].length, 4)}`, { key: index }, inline(heading[2]))); index += 1; continue; }
    if (line.includes('|') && index + 1 < lines.length && /^\s*\|?\s*:?-{3,}/.test(lines[index + 1])) {
      const headers = cells(line); index += 2; const rows: string[][] = [];
      while (index < lines.length && lines[index].includes('|') && lines[index].trim()) rows.push(cells(lines[index++]));
      blocks.push(<div key={blockKey} className="markdown-table"><table><thead><tr>{headers.map((cell, key) => <th key={key}>{inline(cell)}</th>)}</tr></thead><tbody>{rows.map((row, key) => <tr key={key}>{row.map((cell, column) => <td key={column}>{inline(cell)}</td>)}</tr>)}</tbody></table></div>); continue;
    }
    if (/^\s*[-*+]\s+/.test(line) || /^\s*\d+[.)]\s+/.test(line)) {
      const ordered = /^\s*\d+[.)]\s+/.test(line); const list: ReactNode[] = [];
      const start = ordered ? Number(line.match(/^\s*(\d+)/)?.[1] || 1) : undefined;
      const pattern = ordered ? /^\s*\d+[.)]\s+/ : /^\s*[-*+]\s+/;
      while (index < lines.length && pattern.test(lines[index])) { list.push(<li key={index}>{inline(lines[index].replace(pattern, ''))}</li>); index += 1; }
      blocks.push(ordered ? <ol key={blockKey} start={start}>{list}</ol> : <ul key={blockKey}>{list}</ul>); continue;
    }
    if (/^>\s?/.test(line)) { blocks.push(<blockquote key={index}>{inline(line.replace(/^>\s?/, ''))}</blockquote>); index += 1; continue; }
    if (/^\s*[-*_]{3,}\s*$/.test(line)) { blocks.push(<hr key={index} />); index += 1; continue; }
    const paragraph = [line]; index += 1;
    while (index < lines.length && lines[index].trim() && !/^(#{1,6}\s|\s*[-*+]\s|\s*\d+[.)]\s|\s*```|>)/.test(lines[index])) paragraph.push(lines[index++]);
    blocks.push(<p key={blockKey}>{inline(paragraph.join('\n'))}</p>);
  }
  return <div className={`markdown-content ${className}`}>{blocks}</div>;
});
