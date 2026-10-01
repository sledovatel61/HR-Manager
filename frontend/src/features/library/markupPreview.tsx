/**
 * Tiny presentational renderer for the validated template markup subset
 * (paragraphs, «- » lists, **bold**).
 *
 * This exists for the manage screen's draft preview only: the library detail
 * screen renders the server-produced fragment (`body_html`), which is the
 * same escape-first pipeline as generated documents. Here React elements are
 * built directly from plain text — no `dangerouslySetInnerHTML`, so even a
 * future renderer change cannot inject markup.
 */
import { Fragment, type ReactNode } from "react";

const BOLD_RE = /\*\*([^\n]+?)\*\*/g;

function renderInline(text: string, keyPrefix: string): ReactNode[] {
  const nodes: ReactNode[] = [];
  let cursor = 0;
  let match: RegExpExecArray | null;
  BOLD_RE.lastIndex = 0;
  while ((match = BOLD_RE.exec(text)) !== null) {
    if (match.index > cursor) {
      nodes.push(text.slice(cursor, match.index));
    }
    nodes.push(<strong key={`${keyPrefix}-b-${match.index}`}>{match[1]}</strong>);
    cursor = match.index + match[0].length;
  }
  if (cursor < text.length) {
    nodes.push(text.slice(cursor));
  }
  return nodes;
}

export function renderMarkupPreview(body: string): ReactNode {
  const lines = body.replace(/\r\n?/g, "\n").split("\n");
  const blocks: ReactNode[] = [];
  let paragraph: string[] = [];
  let list: string[] = [];

  const flushParagraph = () => {
    if (paragraph.length > 0) {
      const key = `p-${blocks.length}`;
      blocks.push(<p key={key}>{renderInline(paragraph.join(" "), key)}</p>);
      paragraph = [];
    }
  };
  const flushList = () => {
    if (list.length > 0) {
      const key = `ul-${blocks.length}`;
      blocks.push(
        <ul key={key}>
          {list.map((item, index) => (
            <li key={`${key}-${index}`}>{renderInline(item, `${key}-${index}`)}</li>
          ))}
        </ul>
      );
      list = [];
    }
  };

  for (const line of lines) {
    const trimmed = line.trim();
    if (!trimmed) {
      flushParagraph();
      flushList();
      continue;
    }
    if (trimmed.startsWith("- ")) {
      flushParagraph();
      list.push(trimmed.slice(2).trim());
    } else {
      flushList();
      paragraph.push(trimmed);
    }
  }
  flushParagraph();
  flushList();

  return <Fragment>{blocks}</Fragment>;
}
