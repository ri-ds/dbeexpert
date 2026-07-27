import { useCallback, useEffect, useState } from 'react';
import { createId } from '../ids';
import type {
  ChatMessage,
  CypherKind,
  CypherResult,
  CypherRow,
  FacultyResult,
  MessageRole,
  QueryMode,
  QueryResponse,
  QueryTimings,
  QueryTrace,
  TraceJudgement,
  TraceStage,
} from '../types';

/**
 * Chat history, owned entirely by the browser. There is no server side store
 * for conversations, so the list lives in localStorage and is validated on the
 * way back in. Nothing about the stored shape is trusted.
 *
 * A conversation id doubles as the backend session id, so reopening a
 * conversation also restores its follow up context on the server for as long as
 * that session lives. When the server has forgotten it, follow up questions
 * simply behave like fresh ones, which is acceptable and not detected here.
 */

const STORAGE_KEY = 'dbe.conversations.v1';

/** Oldest conversations beyond this are dropped. */
const MAX_CONVERSATIONS = 50;

/** Roughly how long a derived title may be before it is cut short. */
const MAX_TITLE = 60;

const FALLBACK_TITLE = 'New conversation';

const EMPTY_MESSAGES: ChatMessage[] = [];

/**
 * Where a conversation's title came from.
 *
 * `derived` is the local fallback, the first message cut short, and it is
 * refreshed as messages change. `generated` is a real name from the backend and
 * is never overwritten, which is what keeps naming to one model call per
 * conversation rather than one per message.
 */
export type TitleSource = 'derived' | 'generated';

export interface Conversation {
  /** Also sent to the backend as the session id. */
  id: string;
  title: string;
  titleSource: TitleSource;
  createdAt: number;
  updatedAt: number;
  messages: ChatMessage[];
}

/* ------------------------------------------------------------------ */
/* Defensive parsing                                                   */
/* ------------------------------------------------------------------ */

function asRecord(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function asString(value: unknown): string | null {
  return typeof value === 'string' ? value : null;
}

function asFiniteNumber(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

function asStringArray(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : [];
}

function asMode(value: unknown): QueryMode {
  return value === 'vector' || value === 'cypher' ? value : 'hybrid';
}

function asRole(value: unknown): MessageRole | null {
  return value === 'user' || value === 'assistant' || value === 'error' ? value : null;
}

function normalizeFaculty(value: unknown): FacultyResult[] {
  if (!Array.isArray(value)) {
    return [];
  }
  const results: FacultyResult[] = [];
  for (const entry of value) {
    const record = asRecord(entry);
    const name = record ? asString(record['name']) : null;
    if (record === null || name === null || name.length === 0) {
      continue;
    }
    results.push({
      name,
      score: asFiniteNumber(record['score']),
      information: asStringArray(record['information']),
    });
  }
  return results;
}

function normalizeCypher(value: unknown): CypherResult | null {
  const record = asRecord(value);
  if (record === null) {
    return null;
  }
  const query = asString(record['query']);
  if (query === null) {
    return null;
  }
  const rows: CypherRow[] = Array.isArray(record['rows'])
    ? record['rows'].reduce<CypherRow[]>((accumulated, row) => {
        const parsed = asRecord(row);
        if (parsed !== null) {
          accumulated.push(parsed);
        }
        return accumulated;
      }, [])
    : [];
  const kind: CypherKind = record['kind'] === 'builtin' ? 'builtin' : 'generated';

  return {
    query,
    params: asRecord(record['params']) ?? {},
    columns: asStringArray(record['columns']),
    rows,
    kind,
    explanation: asString(record['explanation']),
  };
}

function normalizeStages(value: unknown): TraceStage[] {
  if (!Array.isArray(value)) {
    return [];
  }
  const stages: TraceStage[] = [];
  for (const entry of value) {
    const record = asRecord(entry);
    const stage = record ? asString(record['stage']) : null;
    if (record === null || stage === null) {
      continue;
    }
    stages.push({
      stage,
      label: asString(record['label']) ?? stage,
      detail: asString(record['detail']),
      ms: asFiniteNumber(record['ms']),
    });
  }
  return stages;
}

function normalizeJudgements(value: unknown): TraceJudgement[] {
  if (!Array.isArray(value)) {
    return [];
  }
  const judgements: TraceJudgement[] = [];
  for (const entry of value) {
    const record = asRecord(entry);
    const name = record ? asString(record['name']) : null;
    if (record === null || name === null || name.length === 0) {
      continue;
    }
    judgements.push({
      name,
      score: asFiniteNumber(record['score']) ?? 0,
      rationale: asString(record['rationale']),
      kept: record['kept'] === true,
    });
  }
  return judgements;
}

function normalizeTrace(value: unknown): QueryTrace | null {
  const record = asRecord(value);
  if (record === null) {
    return null;
  }
  return {
    stages: normalizeStages(record['stages']),
    retrievedChunks: asFiniteNumber(record['retrievedChunks']),
    judged: asFiniteNumber(record['judged']),
    kept: asFiniteNumber(record['kept']),
    cutoff: asString(record['cutoff']),
    intent: asString(record['intent']),
    skill: asString(record['skill']),
    coverage: asString(record['coverage']),
    judgements: normalizeJudgements(record['judgements']),
    noEvidence: asStringArray(record['noEvidence']),
  };
}

function normalizeTimings(value: unknown): QueryTimings | null {
  const record = asRecord(value);
  if (record === null) {
    return null;
  }
  const totalMs = asFiniteNumber(record['totalMs']);
  return totalMs === null ? null : { totalMs };
}

function normalizeResponse(value: unknown): QueryResponse | null {
  const record = asRecord(value);
  if (record === null) {
    return null;
  }
  return {
    mode: asMode(record['mode']),
    questionType: asString(record['questionType']),
    intent: asString(record['intent']),
    agent: asString(record['agent']),
    answerText: asString(record['answerText']),
    faculty: normalizeFaculty(record['faculty']),
    cypher: normalizeCypher(record['cypher']),
    trace: normalizeTrace(record['trace']),
    timings: normalizeTimings(record['timings']),
    sessionId: asString(record['sessionId']) ?? '',
  };
}

/**
 * Rebuild one message from stored data. Streaming state is never restored, so a
 * reloaded conversation cannot show a stuck spinner.
 */
function normalizeMessage(value: unknown): ChatMessage | null {
  const record = asRecord(value);
  if (record === null) {
    return null;
  }
  const role = asRole(record['role']);
  if (role === null) {
    return null;
  }
  const text = asString(record['text']);
  const response = normalizeResponse(record['response']);

  // A message with neither text nor a response would render as an empty slot.
  if (text === null && response === null) {
    return null;
  }

  const rawMode = record['mode'];
  return {
    id: asString(record['id']) ?? createId(),
    role,
    createdAt: asFiniteNumber(record['createdAt']) ?? Date.now(),
    text,
    response,
    mode: typeof rawMode === 'string' ? asMode(rawMode) : null,
    stages: [],
    ranked: [],
    pending: false,
  };
}

function normalizeConversation(value: unknown): Conversation | null {
  const record = asRecord(value);
  if (record === null) {
    return null;
  }
  const id = asString(record['id']);
  if (id === null || id.length === 0) {
    return null;
  }
  // A messages field that is not a list means the entry is corrupt. Dropping it
  // is better than keeping a husk that would sort ahead of real conversations.
  const raw = record['messages'];
  if (!Array.isArray(raw)) {
    return null;
  }
  const messages: ChatMessage[] = raw.reduce<ChatMessage[]>((accumulated, entry) => {
    const message = normalizeMessage(entry);
    if (message !== null) {
      accumulated.push(message);
    }
    return accumulated;
  }, []);
  if (messages.length === 0) {
    // Empty conversations are never written, so one here is a leftover.
    return null;
  }

  const createdAt = asFiniteNumber(record['createdAt']) ?? Date.now();
  const storedTitle = asString(record['title'])?.trim() ?? '';
  // A generated title survives a reload, so returning to an old conversation
  // never spends another call renaming something already named.
  const generated = record['titleSource'] === 'generated' && storedTitle.length > 0;

  return {
    id,
    title: storedTitle.length > 0 ? storedTitle : deriveTitle(messages),
    titleSource: generated ? 'generated' : 'derived',
    createdAt,
    updatedAt: asFiniteNumber(record['updatedAt']) ?? createdAt,
    messages,
  };
}

/* ------------------------------------------------------------------ */
/* Storage                                                             */
/* ------------------------------------------------------------------ */

function readConversations(): Conversation[] {
  let raw: string | null = null;
  try {
    raw = window.localStorage.getItem(STORAGE_KEY);
  } catch {
    // Storage is unavailable, for example in private browsing. Stay in memory.
    return [];
  }
  if (raw === null || raw.length === 0) {
    return [];
  }

  let parsed: unknown = null;
  try {
    parsed = JSON.parse(raw) as unknown;
  } catch {
    // Corrupt payload. Start clean rather than crash.
    return [];
  }
  if (!Array.isArray(parsed)) {
    return [];
  }

  const conversations: Conversation[] = [];
  const seen = new Set<string>();
  for (const entry of parsed) {
    const conversation = normalizeConversation(entry);
    if (conversation === null || seen.has(conversation.id)) {
      continue;
    }
    seen.add(conversation.id);
    conversations.push(conversation);
  }
  return sortConversations(conversations).slice(0, MAX_CONVERSATIONS);
}

/**
 * Strip everything that only makes sense during a live request: the stage list,
 * the live ranking, and any message still in flight. Only completed content and
 * responses are written, so a reloaded conversation can never show a spinner
 * for a request that is long gone.
 *
 * Conversations left with nothing in them are not written at all, which keeps
 * an untouched New Chat out of the history.
 */
function toStored(conversations: Conversation[]): Conversation[] {
  return conversations
    .map((conversation) => ({
      ...conversation,
      messages: conversation.messages
        .filter((message) => !message.pending)
        .map((message) => ({
          ...message,
          stages: [],
          ranked: [],
          pending: false,
        })),
    }))
    .filter((conversation) => conversation.messages.length > 0);
}

function writeConversations(conversations: Conversation[]): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(toStored(conversations)));
  } catch {
    // Quota exceeded or storage blocked. Persisting is best effort only.
  }
}

/* ------------------------------------------------------------------ */
/* Helpers                                                             */
/* ------------------------------------------------------------------ */

function sortConversations(conversations: Conversation[]): Conversation[] {
  return conversations
    .slice()
    .sort((a, b) => b.updatedAt - a.updatedAt || b.createdAt - a.createdAt);
}

/** Title from the first user message, cut to a scannable length. */
export function deriveTitle(messages: ChatMessage[]): string {
  const first = messages.find(
    (message) => message.role === 'user' && typeof message.text === 'string',
  );
  const text = first?.text?.replace(/\s+/g, ' ').trim() ?? '';
  if (text.length === 0) {
    return FALLBACK_TITLE;
  }
  if (text.length <= MAX_TITLE) {
    return text;
  }
  return `${text.slice(0, MAX_TITLE).trimEnd()}…`;
}

function createConversation(): Conversation {
  const now = Date.now();
  return {
    id: createId(),
    title: FALLBACK_TITLE,
    titleSource: 'derived',
    createdAt: now,
    updatedAt: now,
    messages: [],
  };
}

interface State {
  conversations: Conversation[];
  activeId: string;
}

function initialState(): State {
  const stored = readConversations();
  const newest = stored[0];
  if (newest !== undefined) {
    // Reopen the most recent conversation rather than adding an empty one on
    // every load. New Chat is one click away.
    return { conversations: stored, activeId: newest.id };
  }
  const fresh = createConversation();
  return { conversations: [fresh], activeId: fresh.id };
}

/* ------------------------------------------------------------------ */
/* Hook                                                               */
/* ------------------------------------------------------------------ */

export interface UseConversationsResult {
  /** Most recently updated first. */
  conversations: Conversation[];
  /** The active conversation id, which is also the backend session id. */
  activeId: string;
  /** Messages of the active conversation. */
  messages: ChatMessage[];
  /**
   * Update one conversation's messages. The id is explicit so a response that
   * arrives after the user switched conversations still lands in the right one.
   */
  updateMessages: (
    conversationId: string,
    updater: (current: ChatMessage[]) => ChatMessage[],
  ) => void;
  /**
   * Apply a generated name. Marks the conversation so its title is never
   * recomputed and never regenerated, including after a reload.
   */
  setGeneratedTitle: (conversationId: string, title: string) => void;
  /** Start a fresh conversation, or reuse the newest one if it is still empty. */
  newConversation: () => void;
  selectConversation: (id: string) => void;
  deleteConversation: (id: string) => void;
}

export function useConversations(): UseConversationsResult {
  const [state, setState] = useState<State>(initialState);

  useEffect(() => {
    writeConversations(state.conversations);
  }, [state.conversations]);

  const updateMessages = useCallback(
    (conversationId: string, updater: (current: ChatMessage[]) => ChatMessage[]) => {
      setState((current) => {
        const index = current.conversations.findIndex(
          (conversation) => conversation.id === conversationId,
        );
        const target = index === -1 ? undefined : current.conversations[index];
        if (target === undefined) {
          return current;
        }
        const messages = updater(target.messages);
        if (messages === target.messages) {
          return current;
        }
        const list = current.conversations.slice();
        list[index] = {
          ...target,
          messages,
          // Only the local fallback tracks the messages. A generated name is
          // left alone, which is what keeps naming to one call per conversation.
          title: target.titleSource === 'generated' ? target.title : deriveTitle(messages),
          updatedAt: Date.now(),
        };
        return { conversations: sortConversations(list), activeId: current.activeId };
      });
    },
    [],
  );

  const setGeneratedTitle = useCallback((conversationId: string, title: string) => {
    const cleaned = title.trim();
    if (cleaned.length === 0) {
      return;
    }
    setState((current) => {
      const index = current.conversations.findIndex(
        (conversation) => conversation.id === conversationId,
      );
      const target = index === -1 ? undefined : current.conversations[index];
      // Already named, so do not churn the list or overwrite it.
      if (target === undefined || target.titleSource === 'generated') {
        return current;
      }
      const list = current.conversations.slice();
      list[index] = { ...target, title: cleaned, titleSource: 'generated' };
      // Renaming is not activity, so updatedAt is left alone and the ordering
      // of the history does not jump around.
      return { conversations: list, activeId: current.activeId };
    });
  }, []);

  const newConversation = useCallback(() => {
    setState((current) => {
      const newest = current.conversations[0];
      if (newest !== undefined && newest.messages.length === 0) {
        // Reuse the empty conversation instead of stacking another one.
        return current.activeId === newest.id ? current : { ...current, activeId: newest.id };
      }
      const fresh = createConversation();
      return {
        conversations: [fresh, ...current.conversations].slice(0, MAX_CONVERSATIONS),
        activeId: fresh.id,
      };
    });
  }, []);

  const selectConversation = useCallback((id: string) => {
    setState((current) => {
      if (current.activeId === id) {
        return current;
      }
      return current.conversations.some((conversation) => conversation.id === id)
        ? { ...current, activeId: id }
        : current;
    });
  }, []);

  const deleteConversation = useCallback((id: string) => {
    setState((current) => {
      const remaining = current.conversations.filter(
        (conversation) => conversation.id !== id,
      );
      if (remaining.length === current.conversations.length) {
        return current;
      }
      if (remaining.length === 0) {
        const fresh = createConversation();
        return { conversations: [fresh], activeId: fresh.id };
      }
      if (current.activeId !== id) {
        return { conversations: remaining, activeId: current.activeId };
      }
      // The list stays sorted, so the head is the next most recent.
      const next = remaining[0];
      return {
        conversations: remaining,
        activeId: next === undefined ? current.activeId : next.id,
      };
    });
  }, []);

  const active = state.conversations.find(
    (conversation) => conversation.id === state.activeId,
  );

  return {
    conversations: state.conversations,
    activeId: state.activeId,
    messages: active?.messages ?? EMPTY_MESSAGES,
    updateMessages,
    setGeneratedTitle,
    newConversation,
    selectConversation,
    deleteConversation,
  };
}
