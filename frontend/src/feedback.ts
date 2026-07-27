/**
 * Building a feedback payload out of an answer the user is already looking at.
 *
 * The point of this module is that the user types a comment and nothing else.
 * The question, a readable rendering of the answer, the routing decision, and
 * the whole pipeline trace are all client side already, so they are attached
 * automatically and shown back to the user before they submit.
 */

import type { FeedbackRequest, QueryResponse } from './types';

/* Limits mirror the backend validators, so a long answer or a pasted essay is
   trimmed here rather than coming back as a 422. */
export const COMMENT_MAX = 8000;
const NAME_MAX = 200;
const QUESTION_MAX = 8000;
const ANSWER_MAX = 40000;
const INTENT_MAX = 40;
const SKILL_MAX = 80;

/** Rows of a Cypher result kept in the snapshot, so one query cannot bloat a row. */
const SNAPSHOT_ROW_CAP = 50;

/**
 * Self reported name, remembered so a second submission does not mean retyping.
 *
 * Temporary. When CCHMC SSO lands the backend fills the user from the session,
 * the client stops sending `userName`, and this pair of helpers plus the name
 * input in FeedbackDialog are deleted together.
 */
const NAME_KEY = 'dbe.feedback.userName';

export function readStoredName(): string {
  try {
    const stored = window.localStorage.getItem(NAME_KEY);
    return typeof stored === 'string' ? stored.slice(0, NAME_MAX) : '';
  } catch {
    // Storage can be unavailable in private browsing modes.
    return '';
  }
}

export function writeStoredName(name: string): void {
  try {
    const cleaned = name.trim();
    if (cleaned.length === 0) {
      window.localStorage.removeItem(NAME_KEY);
      return;
    }
    window.localStorage.setItem(NAME_KEY, cleaned.slice(0, NAME_MAX));
  } catch {
    // Persisting is best effort only.
  }
}

/* ------------------------------------------------------------------ */
/* Attached context                                                    */
/* ------------------------------------------------------------------ */

/** Trim a value the backend may send as null, an empty string, or padded. */
function text(value: string | null | undefined): string {
  return typeof value === 'string' ? value.trim() : '';
}

function clamp(value: string, max: number): string {
  return value.length <= max ? value : value.slice(0, max);
}

/**
 * Flatten an answer into plain text a reviewer can read on its own.
 *
 * The prose answer comes first when there is one, then each faculty entry with
 * its score and its evidence bullets. A question can produce either or both, so
 * neither half is assumed to be present.
 */
export function renderAnswerText(response: QueryResponse | null): string {
  if (response === null) {
    return '';
  }

  const sections: string[] = [];

  const prose = text(response.answerText);
  if (prose.length > 0) {
    sections.push(prose);
  }

  const faculty = Array.isArray(response.faculty) ? response.faculty : [];
  const entries: string[] = [];
  for (const item of faculty) {
    const name = text(item?.name);
    if (name.length === 0) {
      continue;
    }
    const score = typeof item.score === 'number' && Number.isFinite(item.score) ? item.score : null;
    const lines: string[] = [score === null ? name : `${name} (relevance ${Math.round(score)})`];
    const information = Array.isArray(item.information) ? item.information : [];
    for (const point of information) {
      const bullet = text(point);
      if (bullet.length > 0) {
        lines.push(`- ${bullet}`);
      }
    }
    entries.push(lines.join('\n'));
  }

  if (entries.length > 0) {
    const heading = entries.length === 1 ? 'Faculty match' : `Faculty matches (${entries.length})`;
    sections.push(`${heading}\n\n${entries.join('\n\n')}`);
  }

  if (sections.length === 0) {
    // Every branch of the answer was empty, which is itself worth reporting.
    return 'The answer contained no prose and no faculty matches.';
  }

  return clamp(sections.join('\n\n'), ANSWER_MAX);
}

/**
 * Everything technical about how the answer was produced, in one object.
 *
 * Cypher rows are capped, with the original count kept alongside so a reviewer
 * can tell a short result from a truncated one. Every field is read defensively
 * because a response restored from localStorage can predate the current shape.
 */
function buildTraceSnapshot(response: QueryResponse | null): Record<string, unknown> | null {
  if (response === null) {
    return null;
  }

  const snapshot: Record<string, unknown> = {
    trace: response.trace ?? null,
    timings: response.timings ?? null,
    questionType: response.questionType ?? null,
    agent: response.agent ?? null,
    sessionId: typeof response.sessionId === 'string' ? response.sessionId : null,
  };

  const cypher = response.cypher ?? null;
  if (cypher !== null) {
    const rows = Array.isArray(cypher.rows) ? cypher.rows : [];
    snapshot['cypher'] = {
      query: typeof cypher.query === 'string' ? cypher.query : '',
      kind: cypher.kind ?? null,
      columns: Array.isArray(cypher.columns) ? cypher.columns : [],
      rows: rows.slice(0, SNAPSHOT_ROW_CAP),
      rowCount: rows.length,
      rowsTruncated: rows.length > SNAPSHOT_ROW_CAP,
    };
  }

  return snapshot;
}

/** The attached half of a submission, shown to the user before they send it. */
export interface FeedbackContext {
  question: string;
  answer: string;
  mode: FeedbackRequest['mode'];
  intent: string | null;
  skill: string | null;
  traceSnapshot: Record<string, unknown> | null;
}

/**
 * Assemble the context for one answer. `question` is the text of the user
 * message immediately before it, which the message itself does not carry.
 */
export function buildFeedbackContext(
  question: string | null,
  response: QueryResponse | null,
): FeedbackContext {
  const intent = text(response?.intent);
  const skill = text(response?.trace?.skill);

  return {
    question: clamp(text(question), QUESTION_MAX),
    answer: renderAnswerText(response),
    mode: response?.mode ?? null,
    intent: intent.length > 0 ? clamp(intent, INTENT_MAX) : null,
    skill: skill.length > 0 ? clamp(skill, SKILL_MAX) : null,
    traceSnapshot: buildTraceSnapshot(response),
  };
}

/**
 * Final request body. Splitting this from the context keeps the one field the
 * user fills in, and the one temporary field, in a single obvious place.
 */
export function buildFeedbackRequest(
  context: FeedbackContext,
  comment: string,
  // Temporary, drop this parameter along with the name input when SSO lands.
  userName: string,
): FeedbackRequest {
  return {
    comment: clamp(comment.trim(), COMMENT_MAX),
    userName: clamp(userName.trim(), NAME_MAX),
    question: context.question,
    answer: context.answer,
    mode: context.mode,
    intent: context.intent,
    skill: context.skill,
    traceSnapshot: context.traceSnapshot,
  };
}
