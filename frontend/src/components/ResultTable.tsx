import type { CypherRow } from '../types';

export interface ResultTableProps {
  columns: string[];
  rows: CypherRow[];
}

/** Render any cell value in a readable way. Objects become compact JSON. */
export function formatCell(value: unknown): { text: string; muted: boolean } {
  if (value === null || value === undefined) {
    return { text: 'null', muted: true };
  }
  if (typeof value === 'string') {
    return value.length === 0
      ? { text: 'empty', muted: true }
      : { text: value, muted: false };
  }
  if (typeof value === 'number') {
    return { text: Number.isFinite(value) ? String(value) : 'not a number', muted: false };
  }
  if (typeof value === 'boolean') {
    return { text: value ? 'true' : 'false', muted: false };
  }
  if (Array.isArray(value)) {
    if (value.length === 0) {
      return { text: 'empty list', muted: true };
    }
    // A list of scalars reads better as a comma separated line.
    const allScalar = value.every(
      (item) =>
        item === null ||
        typeof item === 'string' ||
        typeof item === 'number' ||
        typeof item === 'boolean',
    );
    if (allScalar) {
      return { text: value.map((item) => String(item)).join(', '), muted: false };
    }
  }
  try {
    return { text: JSON.stringify(value) ?? String(value), muted: false };
  } catch {
    return { text: String(value), muted: false };
  }
}

/**
 * Tabular Cypher results. Scrolls horizontally inside its own container, has a
 * sticky header, zebra rows, and a row count caption.
 */
export default function ResultTable({ columns, rows }: ResultTableProps) {
  // Fall back to the union of row keys if the backend omitted columns.
  const headers =
    columns.length > 0
      ? columns
      : Array.from(new Set(rows.flatMap((row) => Object.keys(row))));

  if (headers.length === 0 || rows.length === 0) {
    return (
      <section className="result-table" aria-label="Query results">
        <p className="result-table__empty">The query returned no rows.</p>
      </section>
    );
  }

  const caption = `${rows.length.toLocaleString('en-US')} ${rows.length === 1 ? 'row' : 'rows'}, ${headers.length} ${headers.length === 1 ? 'column' : 'columns'}`;

  return (
    <section className="result-table" aria-label="Query results">
      <div className="result-table__scroll" tabIndex={0} role="group" aria-label="Scrollable results table">
        <table>
          <caption className="sr-only">Cypher query results, {caption}</caption>
          <thead>
            <tr>
              {headers.map((column) => (
                <th key={column} scope="col">
                  {column}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, rowIndex) => (
              <tr key={rowIndex}>
                {headers.map((column) => {
                  const cell = formatCell(row[column]);
                  return (
                    <td key={column} className={cell.muted ? 'is-empty' : undefined}>
                      {cell.text}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="result-table__caption">{caption}</p>
    </section>
  );
}
