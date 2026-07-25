import type { HealthResponse } from '../types';

export type HealthState = 'loading' | 'ok' | 'degraded' | 'offline';

export interface StatusPillProps {
  state: HealthState;
  health: HealthResponse | null;
}

/** Work out what to say about the backend from the health payload. */
export function describeHealth(
  state: HealthState,
  health: HealthResponse | null,
): { tone: 'neutral' | 'ok' | 'warn' | 'bad'; short: string; long: string } {
  if (state === 'loading') {
    return {
      tone: 'neutral',
      short: 'Checking',
      long: 'Checking the backend connection.',
    };
  }
  if (state === 'offline' || health === null) {
    return {
      tone: 'bad',
      short: 'Offline',
      long: 'The backend is unreachable. Answers are unavailable until it responds.',
    };
  }

  const neo4jDown = !health.neo4j.connected;
  const openAiDown = !health.openai.configured;

  if (neo4jDown && openAiDown) {
    return {
      tone: 'bad',
      short: 'Not configured',
      long: 'Neither the graph database nor the language model is available.',
    };
  }
  if (neo4jDown) {
    return {
      tone: 'warn',
      short: 'Graph offline',
      long: 'The Neo4j knowledge graph is not connected.',
    };
  }
  if (openAiDown) {
    return {
      tone: 'warn',
      short: 'Model offline',
      long: 'The OpenAI credentials are not configured.',
    };
  }
  return {
    tone: 'ok',
    short: 'Connected',
    long: `Graph and model ready. Chat model ${health.openai.chatModel}.`,
  };
}

/**
 * Compact connection indicator for the sticky header. The dot carries color,
 * the text carries the same meaning so color is never the only signal.
 */
export default function StatusPill({ state, health }: StatusPillProps) {
  const { tone, short, long } = describeHealth(state, health);

  return (
    <span
      className={`status-pill status-pill--${tone}`}
      title={long}
      role="status"
      aria-live="polite"
    >
      <span
        className={`status-pill__dot${state === 'loading' ? ' status-pill__dot--pulse' : ''}`}
        aria-hidden="true"
      />
      <span className="status-pill__text">{short}</span>
      <span className="sr-only">. {long}</span>
    </span>
  );
}
