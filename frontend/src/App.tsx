import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  describeError,
  fetchHealth,
  fetchMeta,
  generateTitle,
  isAbortError,
  streamQuery,
} from './api';
import ChatThread from './components/ChatThread';
import Composer, { type ComposerHandle } from './components/Composer';
import { AlertIcon, InfoIcon, MenuIcon } from './components/Icons';
import InfoDialog from './components/InfoDialog';
import Sidebar from './components/Sidebar';
import StatusPill, { type HealthState } from './components/StatusPill';
import UserChip from './components/UserChip';
import { useConversations } from './hooks/useConversations';
import { useTheme } from './hooks/useTheme';
import { createId } from './ids';
import type {
  ChatMessage,
  HealthResponse,
  MetaResponse,
  ModeInfo,
  QueryMode,
  QueryResponse,
  RankedFaculty,
  StageEvent,
} from './types';

/** Shown until /api/meta responds, so the mode control is never empty. */
const FALLBACK_MODES: ModeInfo[] = [
  {
    id: 'hybrid',
    label: 'Hybrid graph search',
    description:
      'Combines semantic retrieval with graph structure, then judges and ranks faculty.',
  },
  {
    id: 'vector',
    label: 'Vector search',
    description: 'Pure embedding similarity across CVs, publications, and abstracts.',
  },
  {
    id: 'cypher',
    label: 'Natural language to Cypher',
    description: 'Translates the question into a Cypher query and returns the rows.',
  },
];

const NARROW_QUERY = '(max-width: 899px)';

export default function App() {
  const theme = useTheme();
  const {
    conversations,
    activeId,
    messages,
    userName,
    logoutUrl,
    updateMessages,
    setGeneratedTitle,
    newConversation,
    selectConversation,
    deleteConversation,
  } = useConversations();

  const [meta, setMeta] = useState<MetaResponse | null>(null);
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [healthState, setHealthState] = useState<HealthState>('loading');

  const [mode, setMode] = useState<QueryMode>('hybrid');
  const [draft, setDraft] = useState('');
  const [busy, setBusy] = useState(false);
  const [elapsedMs, setElapsedMs] = useState(0);

  const [isNarrow, setIsNarrow] = useState<boolean>(() =>
    typeof window.matchMedia === 'function' ? window.matchMedia(NARROW_QUERY).matches : false,
  );
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [infoOpen, setInfoOpen] = useState(false);

  // Conversations already named, or already being named. Naming costs a model
  // call, so it happens at most once per conversation for the life of the tab.
  const titledRef = useRef<Set<string>>(new Set());

  const abortRef = useRef<AbortController | null>(null);
  const startedAtRef = useRef<number>(0);
  const composerRef = useRef<ComposerHandle | null>(null);
  const menuBtnRef = useRef<HTMLButtonElement | null>(null);
  const infoBtnRef = useRef<HTMLButtonElement | null>(null);
  const drawerRef = useRef<HTMLDivElement | null>(null);

  /* ---------------------------------------------------------------- */
  /* Viewport                                                          */
  /* ---------------------------------------------------------------- */

  useEffect(() => {
    if (typeof window.matchMedia !== 'function') {
      return;
    }
    const query = window.matchMedia(NARROW_QUERY);

    // Read the current value rather than trusting the event, and listen on
    // resize as well. A missed change event would otherwise leave the sidebar
    // stuck as a drawer on a wide screen.
    const sync = (): void => {
      const narrow = query.matches;
      setIsNarrow(narrow);
      if (!narrow) {
        setDrawerOpen(false);
      }
    };

    sync();
    query.addEventListener('change', sync);
    window.addEventListener('resize', sync);
    return () => {
      query.removeEventListener('change', sync);
      window.removeEventListener('resize', sync);
    };
  }, []);

  // Conversations restored from storage that already carry a generated name are
  // marked here, so reopening an old chat never spends a call renaming it.
  useEffect(() => {
    for (const conversation of conversations) {
      if (conversation.titleSource === 'generated') {
        titledRef.current.add(conversation.id);
      }
    }
  }, [conversations]);

  /* ---------------------------------------------------------------- */
  /* Metadata and health                                               */
  /* ---------------------------------------------------------------- */

  useEffect(() => {
    const controller = new AbortController();

    void (async () => {
      try {
        const result = await fetchMeta(controller.signal);
        setMeta(result);
        // Keep the current mode if the backend still offers it.
        if (result.modes.length > 0) {
          setMode((current) =>
            result.modes.some((item) => item.id === current)
              ? current
              : (result.modes[0]?.id ?? current),
          );
        }
      } catch (error) {
        if (!isAbortError(error)) {
          // Metadata is optional, the app stays usable with the fallbacks.
          setMeta(null);
        }
      }
    })();

    return () => {
      controller.abort();
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    const controller = new AbortController();

    const check = async (): Promise<void> => {
      try {
        const result = await fetchHealth(controller.signal);
        if (cancelled) {
          return;
        }
        setHealth(result);
        setHealthState(
          result.neo4j.connected && result.openai.configured ? 'ok' : 'degraded',
        );
      } catch (error) {
        if (cancelled || isAbortError(error)) {
          return;
        }
        setHealth(null);
        setHealthState('offline');
      }
    };

    void check();
    // Re check periodically so a backend that comes up later is noticed.
    const timer = window.setInterval(() => void check(), 30000);

    return () => {
      cancelled = true;
      controller.abort();
      window.clearInterval(timer);
    };
  }, []);

  /* ---------------------------------------------------------------- */
  /* Elapsed timer while streaming                                     */
  /* ---------------------------------------------------------------- */

  useEffect(() => {
    if (!busy) {
      return;
    }
    const timer = window.setInterval(() => {
      setElapsedMs(Date.now() - startedAtRef.current);
    }, 200);
    return () => {
      window.clearInterval(timer);
    };
  }, [busy]);

  // Return focus to the composer once a request settles. This has to wait for
  // the render that re enables the textarea, since a disabled field cannot
  // take focus.
  const wasBusyRef = useRef(false);
  useEffect(() => {
    if (wasBusyRef.current && !busy) {
      composerRef.current?.focus();
    }
    wasBusyRef.current = busy;
  }, [busy]);

  /* ---------------------------------------------------------------- */
  /* Message helpers                                                   */
  /* ---------------------------------------------------------------- */

  /**
   * Every helper takes the conversation the request belongs to, so a late
   * arriving event cannot write into whichever conversation happens to be open
   * by the time it lands.
   */
  const patchMessage = useCallback(
    (conversationId: string, id: string, patch: (message: ChatMessage) => ChatMessage) => {
      updateMessages(conversationId, (current) =>
        current.map((message) => (message.id === id ? patch(message) : message)),
      );
    },
    [updateMessages],
  );

  /** Merge one stage event into a message's stage list. */
  const applyStage = useCallback(
    (conversationId: string, id: string, event: StageEvent) => {
      const now = Date.now();
      patchMessage(conversationId, id, (message) => {
        const stages = message.stages.slice();
        const last = stages.length > 0 ? stages[stages.length - 1] : undefined;

        if (last && last.stage === event.stage) {
          // Same stage reporting new progress or detail.
          stages[stages.length - 1] = {
            ...last,
            label: event.label ?? last.label,
            detail: event.detail ?? last.detail,
            progress: event.progress ?? last.progress,
          };
          return { ...message, stages };
        }

        if (last) {
          stages[stages.length - 1] = {
            ...last,
            status: 'complete',
            ms: last.ms ?? now - last.startedAt,
          };
        }

        stages.push({
          stage: event.stage,
          label: event.label ?? event.stage,
          detail: event.detail ?? null,
          progress: event.progress ?? null,
          status: 'active',
          startedAt: now,
          ms: null,
        });
        return { ...message, stages };
      });
    },
    [patchMessage],
  );

  const finishStages = useCallback(
    (conversationId: string, id: string) => {
      const now = Date.now();
      patchMessage(conversationId, id, (message) => ({
        ...message,
        stages: message.stages.map((stage) =>
          stage.status === 'complete'
            ? stage
            : { ...stage, status: 'complete' as const, ms: stage.ms ?? now - stage.startedAt },
        ),
      }));
    },
    [patchMessage],
  );

  /* ---------------------------------------------------------------- */
  /* Sending                                                           */
  /* ---------------------------------------------------------------- */

  const send = useCallback(
    (rawQuestion: string) => {
      const question = rawQuestion.trim();
      if (question.length === 0 || busy) {
        return;
      }

      // The conversation id is also the backend session id, which is what ties
      // a follow up question to the exchange before it.
      const conversationId = activeId;

      const userMessage: ChatMessage = {
        id: createId(),
        role: 'user',
        createdAt: Date.now(),
        text: question,
        response: null,
        mode,
        stages: [],
        ranked: [],
        pending: false,
      };
      const assistantId = createId();
      const assistantMessage: ChatMessage = {
        id: assistantId,
        role: 'assistant',
        createdAt: Date.now(),
        text: null,
        response: null,
        mode,
        stages: [],
        ranked: [],
        pending: true,
      };

      updateMessages(conversationId, (current) => [
        ...current,
        userMessage,
        assistantMessage,
      ]);
      setDraft('');
      setBusy(true);
      startedAtRef.current = Date.now();
      setElapsedMs(0);

      const controller = new AbortController();
      abortRef.current = controller;

      // Held in an object so the callbacks and the finally block share state
      // without relying on narrowing of a captured local.
      const outcome: { failure: string | null; gotResult: boolean } = {
        failure: null,
        gotResult: false,
      };

      void streamQuery(
        {
          question,
          mode,
          sessionId: conversationId,
          agent: null,
        },
        {
          onStage: (event) => applyStage(conversationId, assistantId, event),
          onTrace: (event) => {
            const ranked: RankedFaculty[] = Array.isArray(event.ranked) ? event.ranked : [];
            patchMessage(conversationId, assistantId, (message) => ({ ...message, ranked }));
          },
          onResult: (response: QueryResponse) => {
            outcome.gotResult = true;
            patchMessage(conversationId, assistantId, (message) => ({
              ...message,
              response,
              mode: response.mode ?? message.mode,
              pending: false,
            }));
            // Name the conversation once, after its first answer lands. The ref
            // guard means a second question never triggers a second call, and
            // the request carries only this question.
            if (!titledRef.current.has(conversationId)) {
              titledRef.current.add(conversationId);
              void generateTitle(question).then((generated) => {
                if (generated.length > 0) {
                  setGeneratedTitle(conversationId, generated);
                }
              });
            }
          },
          onError: (event) => {
            outcome.failure = event.message;
          },
        },
        controller.signal,
      )
        .catch((error: unknown) => {
          if (!isAbortError(error)) {
            outcome.failure = describeError(error);
          }
        })
        .finally(() => {
          finishStages(conversationId, assistantId);
          const aborted = controller.signal.aborted;

          if (!outcome.gotResult) {
            // Replace the empty assistant slot with an explanation.
            updateMessages(conversationId, (current) =>
              current.map((message) =>
                message.id === assistantId
                  ? {
                      ...message,
                      role: 'error' as const,
                      pending: false,
                      text:
                        outcome.failure ??
                        (aborted
                          ? 'Stopped before an answer was returned.'
                          : 'The server closed the connection before returning an answer.'),
                    }
                  : message,
              ),
            );
          } else {
            patchMessage(conversationId, assistantId, (message) => ({
              ...message,
              pending: false,
            }));
            if (outcome.failure !== null) {
              const failure = outcome.failure;
              updateMessages(conversationId, (current) => [
                ...current,
                {
                  id: createId(),
                  role: 'error',
                  createdAt: Date.now(),
                  text: failure,
                  response: null,
                  mode,
                  stages: [],
                  ranked: [],
                  pending: false,
                },
              ]);
            }
          }

          abortRef.current = null;
          setBusy(false);
        });
    },
    [
      activeId,
      applyStage,
      busy,
      finishStages,
      mode,
      patchMessage,
      setGeneratedTitle,
      updateMessages,
    ],
  );

  const stop = useCallback(() => {
    abortRef.current?.abort();
  }, []);

  // Abort any in flight request if the app unmounts.
  useEffect(
    () => () => {
      abortRef.current?.abort();
    },
    [],
  );

  /* ---------------------------------------------------------------- */
  /* Conversations                                                     */
  /* ---------------------------------------------------------------- */

  // Switching conversations stops the current request. Its stages belong to the
  // conversation being left, and letting it run would leave the composer
  // disabled in the one being opened.
  const leaveCurrent = useCallback(() => {
    abortRef.current?.abort();
    setBusy(false);
    setElapsedMs(0);
    setDrawerOpen(false);
  }, []);

  const newChat = useCallback(() => {
    leaveCurrent();
    setDraft('');
    newConversation();
    composerRef.current?.focus();
  }, [leaveCurrent, newConversation]);

  const openConversation = useCallback(
    (id: string) => {
      if (id === activeId) {
        setDrawerOpen(false);
        // Reopening the conversation that is already open is normally a no-op.
        // The exception is one showing nothing, where a fetch failed: clicking it
        // is the only retry a user has, so let that case through.
        const active = conversations.find((conversation) => conversation.id === id);
        if (active !== undefined && active.messages.length === 0) {
          selectConversation(id);
        }
        return;
      }
      leaveCurrent();
      selectConversation(id);
    },
    [activeId, conversations, leaveCurrent, selectConversation],
  );

  const removeConversation = useCallback(
    (id: string) => {
      if (id === activeId) {
        abortRef.current?.abort();
        setBusy(false);
        setElapsedMs(0);
      }
      deleteConversation(id);
    },
    [activeId, deleteConversation],
  );

  const pickExample = useCallback((question: string) => {
    setDraft(question);
    setDrawerOpen(false);
    composerRef.current?.focus();
  }, []);

  /* ---------------------------------------------------------------- */
  /* Drawer and dialog behaviour                                       */
  /* ---------------------------------------------------------------- */

  const closeDrawer = useCallback(() => {
    setDrawerOpen(false);
    // Return focus to the control that opened the drawer.
    window.setTimeout(() => menuBtnRef.current?.focus(), 0);
  }, []);

  useEffect(() => {
    if (!drawerOpen) {
      return;
    }
    const onKeyDown = (event: KeyboardEvent): void => {
      if (event.key === 'Escape') {
        event.preventDefault();
        closeDrawer();
      }
    };
    document.addEventListener('keydown', onKeyDown);
    // Move focus into the drawer so keyboard users are not left behind.
    const first = drawerRef.current?.querySelector<HTMLElement>(
      'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])',
    );
    first?.focus();
    return () => {
      document.removeEventListener('keydown', onKeyDown);
    };
  }, [closeDrawer, drawerOpen]);

  // Lock background scrolling while the drawer covers the page.
  useEffect(() => {
    if (!drawerOpen) {
      return;
    }
    const previous = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      document.body.style.overflow = previous;
    };
  }, [drawerOpen]);

  const openInfo = useCallback(() => {
    // Only one overlay at a time, otherwise the two scroll locks fight.
    setDrawerOpen(false);
    setInfoOpen(true);
  }, []);

  const closeInfo = useCallback(() => {
    setInfoOpen(false);
    // Return focus to the trigger, which the dialog cannot know about.
    window.setTimeout(() => infoBtnRef.current?.focus(), 0);
  }, []);

  /* ---------------------------------------------------------------- */
  /* Derived values                                                    */
  /* ---------------------------------------------------------------- */

  // The active mode is resolved inside Composer, which is the only place that
  // shows it now that the header carries the conversation name instead.
  const modes = meta?.modes && meta.modes.length > 0 ? meta.modes : FALLBACK_MODES;
  const faculty = meta?.faculty ?? [];
  const graph = meta?.graph ?? null;
  const documentCategories = meta?.documentCategories ?? [];

  // The conversation's own name, once it has one. An untouched chat has no
  // content to name yet, so it falls back to the app name.
  const headerTitle = useMemo(() => {
    const active = conversations.find((conversation) => conversation.id === activeId);
    if (active === undefined) {
      return 'Expertise Explorer';
    }
    // messageCount covers the moment on load where a saved conversation is open
    // but its messages are still being fetched. Without it the header falls back
    // to the app name and then snaps to the real title.
    const hasContent = active.messages.length > 0 || (active.messageCount ?? 0) > 0;
    if (!hasContent) {
      return 'Expertise Explorer';
    }
    return active.title.trim().length > 0 ? active.title : 'Expertise Explorer';
  }, [activeId, conversations]);

  const warning = useMemo(() => {
    if (healthState === 'offline') {
      return 'The backend is not responding. Start the API on port 8011, then this banner will clear on its own.';
    }
    if (healthState !== 'degraded' || health === null) {
      return null;
    }
    const problems: string[] = [];
    if (!health.neo4j.connected) {
      problems.push(
        health.neo4j.error
          ? `the Neo4j knowledge graph is not connected (${health.neo4j.error})`
          : 'the Neo4j knowledge graph is not connected',
      );
    }
    if (!health.openai.configured) {
      problems.push('the OpenAI API key is not configured');
    }
    if (problems.length === 0) {
      return null;
    }
    return `Answers will fail because ${problems.join(' and ')}.`;
  }, [health, healthState]);

  const blocked = healthState === 'offline';

  const sidebar = (
    <Sidebar
      conversations={conversations}
      activeId={activeId}
      onSelectConversation={openConversation}
      onDeleteConversation={removeConversation}
      onNewChat={newChat}
      themeMode={theme.mode}
      themeResolved={theme.resolved}
      setThemeMode={theme.setMode}
      isDrawer={isNarrow}
      onClose={closeDrawer}
    />
  );

  return (
    <div className={`shell${isNarrow ? ' shell--narrow' : ''}`}>
      {isNarrow ? (
        <>
          {drawerOpen ? (
            <div className="backdrop" onClick={closeDrawer} aria-hidden="true" />
          ) : null}
          {/* When closed the drawer is visibility hidden in CSS, which also
              removes its contents from the tab order. */}
          <div
            className={`drawer${drawerOpen ? ' is-open' : ''}`}
            ref={drawerRef}
            role="dialog"
            aria-modal="true"
            aria-label="Menu"
            aria-hidden={drawerOpen ? undefined : true}
          >
            {sidebar}
          </div>
        </>
      ) : (
        sidebar
      )}

      <main className="main" aria-label="Faculty expertise chat">
        <header className="topbar">
          {isNarrow ? (
            <button
              type="button"
              className="icon-btn"
              ref={menuBtnRef}
              onClick={() => setDrawerOpen(true)}
              aria-label="Open the menu"
              aria-expanded={drawerOpen}
            >
              <MenuIcon size={18} />
            </button>
          ) : null}

          {/* The header names the conversation you are in, not the app. The app
              name is in the sidebar and the retrieval mode is on the composer,
              so repeating either here would only crowd it out. */}
          <div className="topbar__titles">
            <h1 className="topbar__title">{headerTitle}</h1>
          </div>

          <div className="topbar__right">
            {/* No New chat button here. The sidebar already has one, and on
                narrow viewports the sidebar is reachable from the menu button. */}
            <button
              type="button"
              className="icon-btn"
              ref={infoBtnRef}
              onClick={openInfo}
              aria-label="About this Explorer"
              aria-haspopup="dialog"
              aria-expanded={infoOpen}
            >
              <InfoIcon size={17} />
            </button>
            {/* The signed in user takes this slot. The connection pill mostly
                said "Connected", which is reassurance rather than information,
                and a real outage already raises the banner below. When health is
                not ok the pill is still shown next to the name, so a problem is
                never hidden by the swap. Anonymous falls back to the pill alone,
                which is the behaviour before identity existed. */}
            {userName ? (
              <>
                {healthState !== 'ok' ? (
                  <StatusPill state={healthState} health={health} />
                ) : null}
                <UserChip name={userName} logoutUrl={logoutUrl} />
              </>
            ) : (
              <StatusPill state={healthState} health={health} />
            )}
          </div>
        </header>

        {warning ? (
          <div className="banner" role="alert">
            <span className="banner__icon" aria-hidden="true">
              <AlertIcon size={15} />
            </span>
            <span className="banner__text">{warning}</span>
          </div>
        ) : null}

        <ChatThread
          signedInAs={userName}
          messages={messages}
          facultyCount={faculty.length}
          onPickExample={pickExample}
          elapsedMs={elapsedMs}
        />

        <div className="composer-slot">
          <div className="composer-slot__inner">
            <Composer
              ref={composerRef}
              value={draft}
              onChange={setDraft}
              onSubmit={() => send(draft)}
              onStop={stop}
              busy={busy}
              blocked={blocked}
              modes={modes}
              mode={mode}
              onModeChange={setMode}
            />
          </div>
        </div>
      </main>

      {infoOpen ? (
        <InfoDialog
          faculty={faculty}
          graph={graph}
          documentCategories={documentCategories}
          onClose={closeInfo}
        />
      ) : null}
    </div>
  );
}
