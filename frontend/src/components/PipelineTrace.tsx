import type {
  LiveStage,
  QueryTimings,
  QueryTrace,
  TraceJudgement,
  TraceStage,
} from '../types';
import { ChevronIcon } from './Icons';
import { formatMs, StageIcon, stageLabel } from './StageIndicator';

export interface PipelineTraceProps {
  trace: QueryTrace | null;
  timings: QueryTimings | null;
  /** Stages observed live, used when the final trace has none. */
  fallbackStages: LiveStage[];
  /** Routing intent from the response, used when the trace omits it. */
  intent?: string | null;
}

interface Row {
  stage: string;
  label: string;
  detail: string | null;
  ms: number | null;
}

/** One key and value line of plain text, for example the cutoff rule. */
interface Fact {
  key: string;
  value: string;
}

function toRows(trace: QueryTrace | null, fallback: LiveStage[]): Row[] {
  const fromTrace: TraceStage[] = trace?.stages ?? [];
  if (fromTrace.length > 0) {
    return fromTrace.map((stage) => ({
      stage: stage.stage,
      label: stageLabel(stage.stage, stage.label),
      detail: stage.detail ?? null,
      ms: typeof stage.ms === 'number' ? stage.ms : null,
    }));
  }
  return fallback.map((stage) => ({
    stage: stage.stage,
    label: stageLabel(stage.stage, stage.label),
    detail: stage.detail,
    ms: stage.ms,
  }));
}

/** Trim a value the backend may send as null, an empty string, or padded. */
function text(value: string | null | undefined): string {
  return typeof value === 'string' ? value.trim() : '';
}

/** Turn a snake case identifier such as roster_or_factual into readable words. */
function humanize(value: string): string {
  return value.replace(/_/g, ' ');
}

/**
 * Collapsed disclosure under an assistant answer showing how the answer was
 * produced: the routing decision, per stage timings, how many chunks were
 * retrieved, how many faculty were judged and kept, the cutoff rule that was
 * applied, and the relevance judge's own notes.
 *
 * Everything here is internal detail. It is deliberately secondary to the
 * answer above it, and the judge's rationales in particular are never rendered
 * as answer content anywhere else.
 */
export default function PipelineTrace({
  trace,
  timings,
  fallbackStages,
  intent = null,
}: PipelineTraceProps) {
  const rows = toRows(trace, fallbackStages);
  const counts: Array<{ label: string; value: string }> = [];

  // Cypher answers skip retrieval and judging entirely, and the backend reports
  // zero for all three in that case. Showing three zeros would imply the phase
  // ran and found nothing, so the whole group is dropped when none has a value.
  const retrieved = trace?.retrievedChunks ?? null;
  const judged = trace?.judged ?? null;
  const kept = trace?.kept ?? null;
  const retrievalRan = [retrieved, judged, kept].some(
    (value) => value !== null && value > 0,
  );

  if (retrievalRan) {
    if (retrieved !== null) {
      counts.push({ label: 'Chunks retrieved', value: retrieved.toLocaleString('en-US') });
    }
    if (judged !== null) {
      counts.push({ label: 'Faculty judged', value: judged.toLocaleString('en-US') });
    }
    if (kept !== null) {
      counts.push({ label: 'Kept', value: kept.toLocaleString('en-US') });
    }
  }
  if (timings && typeof timings.totalMs === 'number') {
    counts.push({ label: 'Total time', value: formatMs(timings.totalMs) });
  }

  /* Routing, coverage, and the cutoff rule, each dropped when absent. */
  const facts: Fact[] = [];

  const routeIntent = text(trace?.intent) || text(intent);
  const skill = text(trace?.skill);
  if (routeIntent.length > 0 || skill.length > 0) {
    const parts: string[] = [];
    if (routeIntent.length > 0) {
      parts.push(`read as ${humanize(routeIntent)}`);
    }
    if (skill.length > 0) {
      parts.push(`answered by the ${humanize(skill)} skill`);
    }
    facts.push({ key: 'Routing', value: parts.join(', ') });
  }

  const coverage = text(trace?.coverage);
  if (coverage.length > 0) {
    facts.push({ key: 'Coverage', value: coverage });
  }

  const cutoff = text(trace?.cutoff);
  if (cutoff.length > 0) {
    facts.push({ key: 'Cutoff', value: cutoff });
  }

  const judgements: TraceJudgement[] = Array.isArray(trace?.judgements)
    ? trace.judgements
    : [];
  const noEvidence: string[] = Array.isArray(trace?.noEvidence)
    ? trace.noEvidence.filter((name) => text(name).length > 0)
    : [];

  if (
    rows.length === 0 &&
    counts.length === 0 &&
    facts.length === 0 &&
    judgements.length === 0 &&
    noEvidence.length === 0
  ) {
    return null;
  }

  return (
    <details className="trace">
      <summary className="trace__summary">
        <span className="trace__chev" aria-hidden="true">
          <ChevronIcon size={13} />
        </span>
        <span className="trace__summary-text">How this answer was produced</span>
        {timings && typeof timings.totalMs === 'number' ? (
          <span className="trace__total">{formatMs(timings.totalMs)}</span>
        ) : null}
      </summary>

      <div className="trace__body">
        {counts.length > 0 ? (
          <dl className="trace__counts">
            {counts.map((item) => (
              <div key={item.label} className="trace__count">
                <dt>{item.label}</dt>
                <dd>{item.value}</dd>
              </div>
            ))}
          </dl>
        ) : null}

        {rows.length > 0 ? (
          <ol className="trace__stages">
            {rows.map((row, index) => (
              <li key={`${row.stage}-${index}`} className="trace__stage">
                <span className="trace__stage-icon" aria-hidden="true">
                  <StageIcon stage={row.stage} size={13} />
                </span>
                <span className="trace__stage-label">{row.label}</span>
                {row.detail ? (
                  <span className="trace__stage-detail">{row.detail}</span>
                ) : null}
                <span className="trace__stage-ms">
                  {row.ms !== null ? formatMs(row.ms) : ''}
                </span>
              </li>
            ))}
          </ol>
        ) : null}

        {facts.length > 0 ? (
          <dl className="trace__facts">
            {facts.map((fact) => (
              <div key={fact.key} className="trace__fact">
                <dt className="trace__fact-key">{fact.key}</dt>
                <dd>{fact.value}</dd>
              </div>
            ))}
          </dl>
        ) : null}

        {judgements.length > 0 ? (
          <section className="trace__judged">
            <h4 className="trace__sub">Relevance judgements</h4>
            <p className="trace__sub-note">
              The relevance judge's own scoring notes, kept here for inspection. These
              are not part of the answer.
            </p>
            <ul className="trace__judgements">
              {judgements.map((item, index) => {
                const rationale = text(item.rationale);
                return (
                  <li
                    key={`${item.name}-${index}`}
                    className={`trace__judgement${item.kept ? ' is-kept' : ''}`}
                  >
                    <p className="trace__judgement-head">
                      <span className="trace__judgement-name">{item.name}</span>
                      {typeof item.score === 'number' && Number.isFinite(item.score) ? (
                        <span className="trace__judgement-score">
                          {Math.round(item.score)}
                          <span className="sr-only"> out of 100</span>
                        </span>
                      ) : null}
                      <span
                        className={`trace__flag trace__flag--${item.kept ? 'kept' : 'dropped'}`}
                      >
                        {item.kept ? 'Kept' : 'Not kept'}
                      </span>
                    </p>
                    {rationale.length > 0 ? (
                      <p className="trace__judgement-why">{rationale}</p>
                    ) : null}
                  </li>
                );
              })}
            </ul>
          </section>
        ) : null}

        {noEvidence.length > 0 ? (
          <p className="trace__note">
            <span className="trace__fact-key">No evidence</span>
            <span>
              {`${noEvidence.join(', ')} passed scoring, but no supporting detail could be extracted.`}
            </span>
          </p>
        ) : null}
      </div>
    </details>
  );
}
