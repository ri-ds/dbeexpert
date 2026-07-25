import { useMemo } from 'react';
import type { CypherKind } from '../types';
import CopyButton from './CopyButton';

/**
 * Cypher keywords that get highlighted. Longer multi word forms come first so
 * that "ORDER BY" wins over a bare "ORDER".
 */
const KEYWORDS = [
  'ORDER BY',
  'OPTIONAL MATCH',
  'GROUP BY',
  'OPTIONAL',
  'DISTINCT',
  'UNWIND',
  'RETURN',
  'CREATE',
  'MERGE',
  'MATCH',
  'WHERE',
  'WITH',
  'LIMIT',
  'COUNT',
  'SKIP',
  'AND',
  'NOT',
  'SET',
  'AS',
  'OR',
];

type TokenKind = 'keyword' | 'string' | 'number' | 'comment' | 'plain';

interface Token {
  kind: TokenKind;
  text: string;
}

/**
 * Tokenize a Cypher query into React friendly pieces.
 *
 * This deliberately produces plain data that is rendered as text nodes inside
 * spans, so nothing is ever injected as HTML and dangerouslySetInnerHTML is
 * never used. Strings and comments are consumed first so keywords inside them
 * are left alone.
 */
export function tokenizeCypher(source: string): Token[] {
  const tokens: Token[] = [];
  let plain = '';

  const flush = (): void => {
    if (plain.length > 0) {
      tokens.push({ kind: 'plain', text: plain });
      plain = '';
    }
  };

  const isWordChar = (char: string | undefined): boolean =>
    char !== undefined && /[A-Za-z0-9_$]/.test(char);

  let i = 0;
  while (i < source.length) {
    const char = source[i] as string;

    // Line comment.
    if (char === '/' && source[i + 1] === '/') {
      const end = source.indexOf('\n', i);
      const stop = end === -1 ? source.length : end;
      flush();
      tokens.push({ kind: 'comment', text: source.slice(i, stop) });
      i = stop;
      continue;
    }

    // Single or double quoted string, with backslash escapes.
    if (char === "'" || char === '"' || char === '`') {
      let j = i + 1;
      while (j < source.length) {
        if (source[j] === '\\') {
          j += 2;
          continue;
        }
        if (source[j] === char) {
          j += 1;
          break;
        }
        j += 1;
      }
      flush();
      tokens.push({ kind: 'string', text: source.slice(i, Math.min(j, source.length)) });
      i = Math.min(j, source.length);
      continue;
    }

    // Number literal, only when it starts a token.
    if (/[0-9]/.test(char) && !isWordChar(source[i - 1])) {
      let j = i;
      while (j < source.length && /[0-9._]/.test(source[j] as string)) {
        j += 1;
      }
      flush();
      tokens.push({ kind: 'number', text: source.slice(i, j) });
      i = j;
      continue;
    }

    // Keyword, matched on a word boundary and case insensitively.
    if (/[A-Za-z]/.test(char) && !isWordChar(source[i - 1])) {
      const upperRest = source.slice(i, i + 16).toUpperCase();
      let matched: string | null = null;
      for (const keyword of KEYWORDS) {
        if (!upperRest.startsWith(keyword)) {
          continue;
        }
        // The character after the keyword must not continue the word.
        const after = source[i + keyword.length];
        if (isWordChar(after)) {
          continue;
        }
        matched = keyword;
        break;
      }
      if (matched !== null) {
        flush();
        tokens.push({ kind: 'keyword', text: source.slice(i, i + matched.length) });
        i += matched.length;
        continue;
      }
      // Not a keyword, consume the whole identifier so it is not rescanned.
      let j = i;
      while (j < source.length && isWordChar(source[j])) {
        j += 1;
      }
      plain += source.slice(i, j);
      i = j;
      continue;
    }

    plain += char;
    i += 1;
  }

  flush();
  return tokens;
}

export interface CypherBlockProps {
  query: string;
  params?: Record<string, unknown>;
  /**
   * `builtin` queries ship with the backend and are vetted, `generated` ones
   * were written by the model for this question. The caption says which, so a
   * reader never mistakes one for the other.
   */
  kind?: CypherKind;
  /** Short note from the backend on what the query does. */
  explanation?: string | null;
}

export default function CypherBlock({
  query,
  params,
  kind = 'generated',
  explanation = null,
}: CypherBlockProps) {
  const tokens = useMemo(() => tokenizeCypher(query), [query]);
  const paramEntries = params ? Object.entries(params) : [];
  const generated = kind !== 'builtin';
  const caption = generated ? 'Generated Cypher' : 'Graph query';
  const note = typeof explanation === 'string' ? explanation.trim() : '';

  return (
    <section className="cypher" aria-label={`${caption}, Cypher`}>
      <header className="cypher__head">
        <div className="cypher__head-row">
          <span className="cypher__caption">{caption}</span>
          <CopyButton
            text={query}
            label={`Copy the ${generated ? 'generated Cypher query' : 'graph query'}`}
          />
        </div>
        {note.length > 0 ? <p className="cypher__explain">{note}</p> : null}
      </header>
      <div className="cypher__scroll">
        <pre className="cypher__pre">
          <code>
            {tokens.map((token, index) =>
              token.kind === 'plain' ? (
                token.text
              ) : (
                <span key={index} className={`cy cy--${token.kind}`}>
                  {token.text}
                </span>
              ),
            )}
          </code>
        </pre>
      </div>
      {paramEntries.length > 0 ? (
        <dl className="cypher__params">
          <dt className="cypher__params-caption">Parameters</dt>
          {paramEntries.map(([key, value]) => (
            <dd key={key} className="cypher__param">
              <span className="cypher__param-key">{key}</span>
              <span className="cypher__param-val">{formatParam(value)}</span>
            </dd>
          ))}
        </dl>
      ) : null}
    </section>
  );
}

function formatParam(value: unknown): string {
  if (value === null) {
    return 'null';
  }
  if (typeof value === 'string') {
    return value;
  }
  if (typeof value === 'number' || typeof value === 'boolean') {
    return String(value);
  }
  try {
    return JSON.stringify(value) ?? String(value);
  } catch {
    return String(value);
  }
}
