import { useCallback, useEffect, useRef, useState } from 'react';
import {
  deleteConversation as apiDeleteConversation,
  ApiError,
  fetchMe,
  getConversation,
  listConversations,
  saveConversation,
} from '../api';
import { createId } from '../ids';
import type {
  ChatMessage,
  ConversationSummary,
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
 * Chat history, in one of two modes decided at startup by /api/me.
 *
 *   server  The CCHMC service provider told us who the user is, so history lives
 *           in Postgres keyed by that account. A different person on the same PC
 *           signs in and sees their own conversations, and history follows them
 *           to any machine.
 *   local   The proxy authenticated the request but forwarded no identity, so
 *           history stays in this browser's localStorage. This is the original
 *           behaviour and the fallback whenever identity or the database is
 *           unavailable, so the app is never broken by their absence.
 *
 * Browser stored data is validated on the way back in and nothing about its
 * shape is trusted, since it can be edited by hand or left over from an older
 * version of the app.
 *
 * Local conversations are deliberately never migrated into a signed in account.
 * On a shared PC they may belong to whoever used it before, and inheriting them
 * is exactly the leak this feature exists to prevent.
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
  /**
   * How many messages the server said this conversation holds, set when it
   * arrives in the list. The list returns summaries, so `messages` is empty
   * until the conversation is opened, and without this there is no way to tell
   * a conversation that has not been fetched from one the user never used.
   * Undefined for conversations created in this browser.
   */
  messageCount?: number;
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

/**
 * A short reason a save failed, fit to show a user.
 *
 * Error bodies from a failing save are not always JSON. A reverse proxy that
 * rejects the request answers with its own HTML page, and putting that in a
 * banner is unreadable, so anything that looks like markup is reduced to the
 * status code alone.
 */
function describeSaveFailure(error: unknown): string {
  if (error instanceof ApiError) {
    const body = error.message.trim();
    const looksLikeMarkup = body.startsWith('<') || /<\/?(html|head|body|center)\b/i.test(body);
    if (body.length === 0 || looksLikeMarkup) {
      return `HTTP ${error.status}`;
    }
    const oneLine = body.replace(/\s+/g, ' ');
    const clipped = oneLine.length > 120 ? `${oneLine.slice(0, 117)}...` : oneLine;
    return `HTTP ${error.status}: ${clipped}`;
  }
  const message = String((error as { message?: string } | null)?.message ?? error).trim();
  if (message.length === 0) {
    return 'unknown error';
  }
  return message.length > 120 ? `${message.slice(0, 117)}...` : message;
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

/**
 * A server summary as a local Conversation.
 *
 * `messages` starts empty because the list endpoint deliberately omits them; a
 * user with a hundred conversations would otherwise transfer megabytes to render
 * a sidebar. They are fetched when the conversation is opened.
 */
function fromSummary(summary: ConversationSummary): Conversation {
  const created = Date.parse(summary.createdAt);
  const updated = Date.parse(summary.updatedAt);
  return {
    id: summary.id,
    title: summary.title || FALLBACK_TITLE,
    titleSource: summary.titleSource === 'generated' ? 'generated' : 'derived',
    createdAt: Number.isFinite(created) ? created : Date.now(),
    updatedAt: Number.isFinite(updated) ? updated : Date.now(),
    messages: [],
    // Kept so the history list can show this conversation before its messages
    // have been fetched. Without it an unopened conversation is indistinguishable
    // from an untouched New Chat and gets filtered out of the sidebar.
    messageCount: Number.isFinite(summary.messageCount) ? summary.messageCount : 0,
  };
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
   * Where history lives. `server` means it is tied to the signed in CCHMC
   * account, so a different person on the same PC sees their own. `local` means
   * the proxy did not tell us who the user is, and it stays in this browser.
   */
  storage: 'local' | 'server';
  /** Display name of the signed in user, when known. */
  userName: string | null;
  /** Where to sign out, when the deployment configured it. */
  logoutUrl: string | null;
  /** True while an opened conversation's messages are still being fetched. */
  loadingConversation: boolean;
  /**
   * Why saving to account history failed, or null while it works. When this is
   * set the hook has already fallen back to browser storage, so nothing is lost,
   * but the user is no longer building history tied to their account and needs
   * telling.
   */
  saveError: string | null;
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

/** How long to wait after the last change before writing to the server. */
const SAVE_DEBOUNCE_MS = 900;

export function useConversations(): UseConversationsResult {
  const [state, setState] = useState<State>(initialState);

  // Which storage is in use. Starts local so the app renders immediately, then
  // flips to server if /api/me says the proxy told us who the user is.
  const [storage, setStorage] = useState<'local' | 'server'>('local');
  const [userName, setUserName] = useState<string | null>(null);
  const [logoutUrl, setLogoutUrl] = useState<string | null>(null);
  const [loadingConversation, setLoadingConversation] = useState(false);

  // Ids whose messages have actually been fetched. In server mode the list
  // arrives as summaries, so an unopened conversation has an empty message array
  // that must not be mistaken for a conversation the user emptied.
  const loadedRef = useRef<Set<string>>(new Set());
  const saveTimersRef = useRef<Map<string, number>>(new Map());
  const storageRef = useRef<'local' | 'server'>('local');

  // Why a server save failed, or null while saving works. Surfaced to the user,
  // because silently not recording their history is the worst of the options.
  const [saveError, setSaveError] = useState<string | null>(null);

  // Latest state, readable from callbacks that close over stale values. Needed so
  // a failed save can write the current conversations to browser storage.
  const stateRef = useRef<State>(state);
  useEffect(() => {
    stateRef.current = state;
  }, [state]);

  useEffect(() => {
    storageRef.current = storage;
  }, [storage]);

  /**
   * Fetch one conversation's messages in server mode.
   *
   * The list endpoint returns summaries only, so every conversation arrives with
   * an empty message array and the content has to be pulled per id. Two callers
   * need it: the sidebar, and the initial load, which must not leave the
   * conversation it opens looking empty.
   *
   * Idempotent. loadedRef stops a second fetch for the same id, and a failure
   * clears the mark so the next attempt retries instead of caching the failure.
   */
  const loadMessages = useCallback((id: string) => {
    if (storageRef.current !== 'server' || loadedRef.current.has(id)) {
      return;
    }
    loadedRef.current.add(id);
    setLoadingConversation(true);
    void getConversation(id)
      .then((detail) => {
        const messages = Array.isArray(detail.messages)
          ? detail.messages.reduce<ChatMessage[]>((accumulated, entry) => {
              const message = normalizeMessage(entry);
              if (message !== null) {
                accumulated.push(message);
              }
              return accumulated;
            }, [])
          : [];
        setState((current) => {
          const index = current.conversations.findIndex(
            (conversation) => conversation.id === id,
          );
          const target = index === -1 ? undefined : current.conversations[index];
          if (target === undefined) {
            return current;
          }
          const list = current.conversations.slice();
          list[index] = { ...target, messages };
          return { ...current, conversations: list };
        });
      })
      .catch(() => {
        // Allow a retry on the next open rather than caching a failure.
        loadedRef.current.delete(id);
      })
      .finally(() => setLoadingConversation(false));
  }, []);

  /* ---------------------------------------------------------------- */
  /* Decide which storage to use, then load from it                    */
  /* ---------------------------------------------------------------- */

  useEffect(() => {
    let cancelled = false;
    const controller = new AbortController();

    void (async () => {
      let me: Awaited<ReturnType<typeof fetchMe>>;
      try {
        me = await fetchMe(controller.signal);
      } catch (error: unknown) {
        // Previously uncaught, which made this an unhandled rejection and left
        // the hook half initialised. Browser storage is the safe fallback.
        if (!cancelled) {
          console.error('Could not determine the signed in user:', error);
          setStorage('local');
          storageRef.current = 'local';
        }
        return;
      }
      if (cancelled) {
        return;
      }
      setUserName(me.displayName);
      setLogoutUrl(me.logoutUrl);

      if (!me.historyEnabled) {
        // Anonymous, or the database is down. Keep the browser local history the
        // app has always used. Nothing changes for the user.
        setStorage('local');
        return;
      }

      setStorage('server');
      // Assigned here as well as through its own effect. That effect does not run
      // until this one has finished, and loadMessages below reads the ref, so
      // leaving it to the effect alone would make the load a silent no-op.
      storageRef.current = 'server';
      try {
        const summaries = await listConversations(controller.signal);
        if (cancelled) {
          return;
        }
        // Deliberately NOT merging anything from localStorage. On a shared PC
        // those chats may belong to whoever used it before, and inheriting them
        // into a named account is exactly the leak this feature exists to fix.
        const list = summaries.map(fromSummary);
        const opening = list[0];
        if (opening === undefined) {
          const fresh = createConversation();
          setState({ conversations: [fresh], activeId: fresh.id });
          return;
        }
        setState({ conversations: list, activeId: opening.id });
        // The list carries summaries only, so the conversation that opens here
        // has no messages yet. Without this fetch it renders blank, which reads
        // as the whole history having been deleted, and clicking it in the
        // sidebar cannot fix it because it is already the active conversation.
        loadMessages(opening.id);
      } catch {
        // Signed in but the list failed. Fall back rather than show nothing.
        if (!cancelled) {
          setStorage('local');
          storageRef.current = 'local';
        }
      }
    })();

    return () => {
      cancelled = true;
      controller.abort();
    };
    // loadMessages is stable, so this still runs once on mount.
  }, [loadMessages]);

  /* ---------------------------------------------------------------- */
  /* Persist                                                           */
  /* ---------------------------------------------------------------- */

  useEffect(() => {
    // Browser storage is only written in local mode. In server mode writing here
    // too would leave a copy of one person's chats on a shared machine.
    if (storage === 'local') {
      writeConversations(state.conversations);
    }
  }, [state.conversations, storage]);

  // Clear any pending saves on unmount so a timer cannot fire after teardown.
  useEffect(
    () => () => {
      for (const timer of saveTimersRef.current.values()) {
        window.clearTimeout(timer);
      }
      saveTimersRef.current.clear();
    },
    [],
  );

  /**
   * Queue a debounced save of one conversation to the server.
   *
   * Debounced because messages change many times per answer as stages stream in,
   * and each change would otherwise be a request. Keyed per conversation so a
   * save for one cannot cancel a save for another.
   */
  const queueSave = useCallback((conversation: Conversation) => {
    if (storageRef.current !== 'server' || conversation.messages.length === 0) {
      return;
    }
    const timers = saveTimersRef.current;
    const existing = timers.get(conversation.id);
    if (existing !== undefined) {
      window.clearTimeout(existing);
    }
    timers.set(
      conversation.id,
      window.setTimeout(() => {
        timers.delete(conversation.id);
        // Strip live streaming state, exactly as the browser path does, so a
        // reopened conversation can never show a spinner for a finished request.
        const messages = conversation.messages
          .filter((message) => !message.pending)
          .map((message) => ({ ...message, stages: [], ranked: [], pending: false }));
        if (messages.length === 0) {
          return;
        }
        void saveConversation({
          id: conversation.id,
          title: conversation.title,
          titleSource: conversation.titleSource,
          messages,
        }).catch((error: unknown) => {
          // A failed save used to be swallowed here. That was wrong: server mode
          // deliberately does not write browser storage, so a rejected save meant
          // the conversation existed nowhere and vanished on the next reload,
          // with nothing shown to the user.
          //
          // Now the failure demotes this session to browser storage. The chat is
          // written locally straight away so it cannot be lost, and the banner
          // tells the user their account history is not recording. One transient
          // failure is enough to demote, on purpose: keeping the data is worth
          // more than staying on the server path.
          const detail = describeSaveFailure(error);
          console.error('Saving to account history failed, falling back to this browser:', error);
          setSaveError(detail);
          setStorage('local');
          storageRef.current = 'local';
          writeConversations(stateRef.current.conversations);
        });
      }, SAVE_DEBOUNCE_MS),
    );
  }, []);

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
        const updated: Conversation = {
          ...target,
          messages,
          // Only the local fallback tracks the messages. A generated name is
          // left alone, which is what keeps naming to one call per conversation.
          title: target.titleSource === 'generated' ? target.title : deriveTitle(messages),
          updatedAt: Date.now(),
        };
        list[index] = updated;
        queueSave(updated);
        return { conversations: sortConversations(list), activeId: current.activeId };
      });
    },
    [queueSave],
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
      const renamed: Conversation = { ...target, title: cleaned, titleSource: 'generated' };
      list[index] = renamed;
      queueSave(renamed);
      // Renaming is not activity, so updatedAt is left alone and the ordering
      // of the history does not jump around.
      return { conversations: list, activeId: current.activeId };
    });
  }, [queueSave]);

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

    loadMessages(id);
  }, [loadMessages]);

  const deleteConversation = useCallback((id: string) => {
    // Cancel a queued save so it cannot resurrect what is being deleted.
    const pending = saveTimersRef.current.get(id);
    if (pending !== undefined) {
      window.clearTimeout(pending);
      saveTimersRef.current.delete(id);
    }
    if (storageRef.current === 'server') {
      // Removed from the UI immediately. A failed delete is not surfaced, since
      // the row reappears on the next load and the user can retry.
      void apiDeleteConversation(id).catch(() => undefined);
    }
    loadedRef.current.delete(id);

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
    storage,
    userName,
    logoutUrl,
    loadingConversation,
    saveError,
    updateMessages,
    setGeneratedTitle,
    newConversation,
    selectConversation,
    deleteConversation,
  };
}
