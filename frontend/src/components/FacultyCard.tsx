import type { FacultyResult } from '../types';
import CopyButton from './CopyButton';

/** Map a relevance score onto one of four semantic bands. */
export function scoreBand(score: number): {
  key: 'strong' | 'good' | 'moderate' | 'weak';
  label: string;
} {
  if (score >= 85) {
    return { key: 'strong', label: 'Strong match' };
  }
  if (score >= 70) {
    return { key: 'good', label: 'Good match' };
  }
  if (score >= 50) {
    return { key: 'moderate', label: 'Moderate match' };
  }
  return { key: 'weak', label: 'Weak match' };
}

/** Flatten a card into plain text for the clipboard. */
function toPlainText(faculty: FacultyResult): string {
  const parts: string[] = [faculty.name];
  if (faculty.score !== null && faculty.score !== undefined) {
    parts.push(`Relevance score: ${faculty.score}`);
  }
  const information = toInformation(faculty);
  if (information.length > 0) {
    parts.push('');
    for (const item of information) {
      parts.push(`- ${item}`);
    }
  }
  return parts.join('\n');
}

/** Read the evidence bullets defensively, since stored data can be stale. */
function toInformation(faculty: FacultyResult): string[] {
  return Array.isArray(faculty.information)
    ? faculty.information.filter((item) => typeof item === 'string' && item.length > 0)
    : [];
}

export interface FacultyCardProps {
  faculty: FacultyResult;
  /** One based position in the result list. */
  rank: number;
}

/**
 * One result card: the name, the relevance score, and the evidence bullets the
 * backend extracted. Nothing else is rendered here. The relevance judge's own
 * prose is internal scoring detail and belongs in the pipeline trace only.
 */
export default function FacultyCard({ faculty, rank }: FacultyCardProps) {
  const hasScore = faculty.score !== null && faculty.score !== undefined;
  const band = hasScore ? scoreBand(faculty.score as number) : null;
  const information = toInformation(faculty);

  return (
    <article className="faculty-card">
      <header className="faculty-card__head">
        <span className="faculty-card__rank" aria-hidden="true">
          {rank}
        </span>
        <h3 className="faculty-card__name">{faculty.name}</h3>
        <div className="faculty-card__actions">
          {band ? (
            <span
              className={`score-badge score-badge--${band.key}`}
              title={`${band.label}, score ${faculty.score}`}
            >
              <span className="score-badge__num">{Math.round(faculty.score as number)}</span>
              <span className="sr-only">
                out of 100. {band.label}.
              </span>
            </span>
          ) : null}
          <CopyButton
            text={toPlainText(faculty)}
            label={`Copy the entry for ${faculty.name}`}
          />
        </div>
      </header>

      {information.length > 0 ? (
        <ul className="faculty-card__points">
          {information.map((item, index) => (
            <li key={index}>
              <span className="faculty-card__bullet" aria-hidden="true" />
              <span>{item}</span>
            </li>
          ))}
        </ul>
      ) : null}
    </article>
  );
}
