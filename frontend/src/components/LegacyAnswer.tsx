import { Fragment, type ReactNode } from 'react';

/**
 * Renders an answer exactly as the original Streamlit app renders it.
 *
 * That app builds its chat bubble with `_md_basic_to_html` (ankitaexpert/app.py:72),
 * which escapes the text and then applies a fixed, ordered set of substitutions:
 * links, inline code, bold, then italics, with newlines becoming <br> inside a
 * `white-space: pre-wrap` block. Notably it does NOT convert "- item" into a
 * list, so bullets stay as literal dashes on their own lines.
 *
 * This mirrors that behaviour so the two apps can be compared side by side.
 * React escapes text for us, so the escaping step is implicit.
 */

/** Ordered to match the original's regex sequence. */
const INLINE = [
  { kind: 'link', pattern: /\[([^\]]+)\]\(([^)]+)\)/ },
  { kind: 'code', pattern: /`([^`]+)`/ },
  { kind: 'strong', pattern: /\*\*(.+?)\*\*/ },
  { kind: 'strong', pattern: /__(.+?)__/ },
  { kind: 'em', pattern: /(?<!_)_(.+?)_(?!_)/ },
] as const;

/**
 * Apply the substitutions to one line, left to right.
 *
 * Each rule is tried in turn on the remaining text. The first that matches wins
 * for that span, and the text after it is processed with the full rule set
 * again, which is what makes bold inside a sentence work.
 */
function renderInline(text: string, keyPrefix: string): ReactNode[] {
  const out: ReactNode[] = [];
  let rest = text;
  let index = 0;

  while (rest.length > 0) {
    let earliest: { at: number; length: number; node: ReactNode } | null = null;

    for (const rule of INLINE) {
      const match = rule.pattern.exec(rest);
      if (match === null || match.index === undefined) continue;
      if (earliest !== null && match.index >= earliest.at) continue;

      const key = `${keyPrefix}-${index}-${rule.kind}-${match.index}`;
      let node: ReactNode;
      if (rule.kind === 'link') {
        node = (
          <a key={key} href={match[2]} target="_blank" rel="noopener noreferrer">
            {match[1]}
          </a>
        );
      } else if (rule.kind === 'code') {
        node = <code key={key}>{match[1]}</code>;
      } else if (rule.kind === 'strong') {
        node = <strong key={key}>{match[1]}</strong>;
      } else {
        node = <em key={key}>{match[1]}</em>;
      }
      earliest = { at: match.index, length: match[0].length, node };
    }

    if (earliest === null) {
      out.push(rest);
      break;
    }

    if (earliest.at > 0) out.push(rest.slice(0, earliest.at));
    out.push(earliest.node);
    rest = rest.slice(earliest.at + earliest.length);
    index += 1;
  }

  return out;
}

export interface LegacyAnswerProps {
  text: string;
}

export default function LegacyAnswer({ text }: LegacyAnswerProps) {
  const lines = text.split('\n');
  return (
    <div className="legacy-answer">
      {lines.map((line, i) => (
        <Fragment key={i}>
          {renderInline(line, String(i))}
          {i < lines.length - 1 ? <br /> : null}
        </Fragment>
      ))}
    </div>
  );
}
