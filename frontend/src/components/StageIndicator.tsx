import type { ComponentType } from 'react';
import type { LiveStage, RankedFaculty } from '../types';
import {
  AnswerIcon,
  CheckIcon,
  ClassifyIcon,
  CypherExecuteIcon,
  CypherGenerateIcon,
  DotIcon,
  ExtractIcon,
  type IconProps,
  JudgeIcon,
  RankIcon,
  RetrieveIcon,
  RouteIcon,
} from './Icons';

/** Friendly fallback labels for every stage id the pipeline can emit. */
const STAGE_LABELS: Record<string, string> = {
  classify: 'Understanding the question',
  route: 'Choosing a search strategy',
  retrieve: 'Searching the knowledge graph',
  judge: 'Judging faculty',
  rank: 'Ranking by relevance',
  extract: 'Extracting supporting detail',
  cypher_generate: 'Writing the Cypher query',
  cypher_execute: 'Running the query',
  graph_query: 'Querying the graph',
  answer: 'Composing the answer',
};

const STAGE_ICONS: Record<string, ComponentType<IconProps>> = {
  classify: ClassifyIcon,
  route: RouteIcon,
  retrieve: RetrieveIcon,
  judge: JudgeIcon,
  rank: RankIcon,
  extract: ExtractIcon,
  cypher_generate: CypherGenerateIcon,
  cypher_execute: CypherExecuteIcon,
  graph_query: CypherExecuteIcon,
  answer: AnswerIcon,
};

/** Backend label when it sent one, otherwise our own friendly wording. */
export function stageLabel(stage: string, label?: string | null): string {
  if (typeof label === 'string' && label.trim().length > 0) {
    return label.trim();
  }
  return STAGE_LABELS[stage] ?? stage.replace(/_/g, ' ');
}

export function StageIcon({ stage, size = 15 }: { stage: string; size?: number }) {
  const Component = STAGE_ICONS[stage] ?? DotIcon;
  return <Component size={size} />;
}

/** Format a duration for display, keeping it short and scannable. */
export function formatMs(ms: number): string {
  if (ms < 1000) {
    return `${Math.round(ms)} ms`;
  }
  const seconds = ms / 1000;
  return `${seconds < 10 ? seconds.toFixed(1) : Math.round(seconds).toString()} s`;
}

export interface StageIndicatorProps {
  stages: LiveStage[];
  ranked: RankedFaculty[];
  /** Elapsed milliseconds since the request started. */
  elapsedMs: number;
}

/**
 * Live pipeline readout shown in the assistant slot while a query runs. This
 * replaces a plain spinner: each stage appears as it is announced, the current
 * one animates, finished ones are checked, and per item progress is counted.
 */
export default function StageIndicator({
  stages,
  ranked,
  elapsedMs,
}: StageIndicatorProps) {
  const current = stages.length > 0 ? stages[stages.length - 1] : undefined;
  const headline =
    current === undefined
      ? 'Starting the pipeline'
      : progressText(current) ?? stageLabel(current.stage, current.label);

  return (
    <div className="stage-panel">
      <div className="stage-panel__head">
        <span className="stage-panel__pulse" aria-hidden="true">
          <span />
          <span />
          <span />
        </span>
        <span className="stage-panel__title">Working on it</span>
        <span className="stage-panel__elapsed">{formatMs(elapsedMs)}</span>
      </div>

      {/* Screen readers get one concise sentence rather than the whole list. */}
      <p className="sr-only" aria-live="polite" aria-atomic="true">
        {headline}
      </p>

      <ol className="stage-list">
        {stages.map((item) => {
          const isActive = item.status === 'active';
          const label = stageLabel(item.stage, item.label);
          const counter = item.progress;
          return (
            <li
              key={`${item.stage}-${item.startedAt}`}
              className={`stage-row${isActive ? ' stage-row--active' : ' stage-row--done'}`}
            >
              <span className="stage-row__mark" aria-hidden="true">
                {isActive ? <StageIcon stage={item.stage} /> : <CheckIcon size={13} />}
              </span>
              <span className="stage-row__body">
                <span className="stage-row__label">
                  {label}
                  {counter ? (
                    <span className="stage-row__counter">
                      {' '}
                      {counter.done} of {counter.total}
                    </span>
                  ) : null}
                </span>
                {item.detail ? (
                  <span className="stage-row__detail">{item.detail}</span>
                ) : null}
                {isActive && counter && counter.total > 0 ? (
                  <span
                    className="stage-row__bar"
                    role="progressbar"
                    aria-label={label}
                    aria-valuemin={0}
                    aria-valuemax={counter.total}
                    aria-valuenow={counter.done}
                  >
                    <span
                      className="stage-row__bar-fill"
                      style={{
                        width: `${Math.min(100, Math.round((counter.done / counter.total) * 100))}%`,
                      }}
                    />
                  </span>
                ) : null}
              </span>
              {/* Live timings under 50 ms are measurement noise, so they are
                  left blank here. The final trace still reports them. */}
              <span className="stage-row__time">
                {item.ms !== null && item.ms >= 50 ? formatMs(item.ms) : ''}
              </span>
            </li>
          );
        })}
      </ol>

      {ranked.length > 0 ? (
        <div className="stage-ranked">
          <span className="stage-ranked__caption">Leading candidates</span>
          <ul className="stage-ranked__list">
            {ranked.slice(0, 8).map((item) => (
              <li key={item.name} className="stage-ranked__item">
                <span className="stage-ranked__name">{item.name}</span>
                {item.score !== null && item.score !== undefined ? (
                  <span className="stage-ranked__score">{Math.round(item.score)}</span>
                ) : null}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}

function progressText(stage: LiveStage): string | null {
  if (!stage.progress) {
    return null;
  }
  return `${stageLabel(stage.stage, stage.label)} ${stage.progress.done} of ${stage.progress.total}`;
}
