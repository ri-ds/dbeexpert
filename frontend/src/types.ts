/**
 * Wire types for the DBE Faculty Expertise Explorer backend.
 * These mirror the API contract exactly. Nothing here is invented.
 */

/* ------------------------------------------------------------------ */
/* GET /api/health                                                     */
/* ------------------------------------------------------------------ */

export interface Neo4jHealth {
  connected: boolean;
  nodes: number;
  relationships: number;
  error: string | null;
}

export interface OpenAiHealth {
  configured: boolean;
  chatModel: string;
  embeddingModel: string;
}

export interface HealthResponse {
  status: string;
  neo4j: Neo4jHealth;
  openai: OpenAiHealth;
  version: string;
}

/* ------------------------------------------------------------------ */
/* GET /api/meta                                                       */
/* ------------------------------------------------------------------ */

export type QueryMode = 'hybrid' | 'vector' | 'cypher';

export interface ModeInfo {
  id: QueryMode;
  label: string;
  description: string;
}

export interface GraphLabelCount {
  label: string;
  count: number;
}

export interface GraphRelTypeCount {
  type: string;
  count: number;
}

export interface GraphStats {
  nodes: number;
  relationships: number;
  labels: GraphLabelCount[];
  relTypes: GraphRelTypeCount[];
}

export interface MetaResponse {
  faculty: string[];
  modes: ModeInfo[];
  agents: string[];
  documentCategories: string[];
  graph: GraphStats;
}

/* ------------------------------------------------------------------ */
/* POST /api/query and POST /api/query/stream                          */
/* ------------------------------------------------------------------ */

export interface QueryRequest {
  question: string;
  mode: QueryMode;
  sessionId: string;
  agent: string | null;
}

/**
 * One faculty member in an answer. `information` holds the extracted evidence
 * bullets, which is the only prose the backend intends for display. The judge's
 * own scoring notes live on the trace and must never be shown as an answer.
 */
export interface FacultyResult {
  name: string;
  score: number | null;
  information: string[];
}

/** A single row from a Cypher result set. Values are untyped by nature. */
export type CypherRow = Record<string, unknown>;

/**
 * `builtin` queries are hand written and vetted, `generated` queries were
 * written by the model for this question. The UI must not present the two as
 * equally trustworthy.
 */
export type CypherKind = 'builtin' | 'generated';

export interface CypherResult {
  query: string;
  params: Record<string, unknown>;
  columns: string[];
  rows: CypherRow[];
  kind: CypherKind;
  explanation: string | null;
  /**
   * False when the prose answer already states the whole result, so rendering a
   * table as well would show the same data twice. Optional so a response from an
   * older backend still renders the table.
   */
  showTable?: boolean;
}

export interface TraceStage {
  stage: string;
  label: string;
  detail: string | null;
  ms: number | null;
}

/** One relevance judge verdict, internal scoring detail rather than an answer. */
export interface TraceJudgement {
  name: string;
  score: number;
  rationale: string | null;
  kept: boolean;
}

export interface QueryTrace {
  stages: TraceStage[];
  retrievedChunks: number | null;
  judged: number | null;
  kept: number | null;
  cutoff: string | null;
  intent: string | null;
  skill: string | null;
  coverage: string | null;
  judgements: TraceJudgement[];
  /** Candidates that passed scoring but yielded no extractable evidence. */
  noEvidence: string[];
}

export interface QueryTimings {
  totalMs: number;
}

export interface QueryResponse {
  mode: QueryMode;
  questionType: string | null;
  /** One of roster, factual, expertise, roster_or_factual, or null. */
  intent: string | null;
  agent: string | null;
  answerText: string | null;
  /**
   * How to render this answer.
   *
   * "legacy" means answerText already holds the complete answer, formatted the
   * way the original Streamlit app formats it, so it is rendered on its own and
   * the faculty cards are suppressed. `faculty` is still populated underneath
   * for feedback reports and the pipeline disclosure.
   *
   * null means the older behaviour: answerText is a lead in, and the cards or
   * the result table carry the answer.
   */
  answerFormat: 'legacy' | null;
  faculty: FacultyResult[];
  cypher: CypherResult | null;
  trace: QueryTrace | null;
  timings: QueryTimings | null;
  sessionId: string;
}

/* ------------------------------------------------------------------ */
/* GET /api/me and the conversation endpoints                           */
/* ------------------------------------------------------------------ */

/**
 * Who the backend thinks you are.
 *
 * `authenticated` false means the reverse proxy let the request through but did
 * not forward a username, so the app cannot tell people apart and keeps history
 * in this browser. `historyEnabled` additionally requires the database to be up.
 */
export interface MeResponse {
  authenticated: boolean;
  userId: string | null;
  displayName: string | null;
  historyEnabled: boolean;
  /** Where to sign out. Null when not configured, so no dead button is shown. */
  logoutUrl: string | null;
}

/** A conversation as the sidebar lists it, without its messages. */
export interface ConversationSummary {
  id: string;
  title: string;
  titleSource: 'derived' | 'generated';
  createdAt: string;
  updatedAt: string;
  messageCount: number;
}

/** One conversation with its messages, as returned when it is opened. */
export interface ConversationDetail extends ConversationSummary {
  messages: unknown[];
}

export interface ConversationListResponse {
  conversations: ConversationSummary[];
}

/* ------------------------------------------------------------------ */
/* POST /api/title                                                     */
/* ------------------------------------------------------------------ */

/**
 * Only the first question is sent. The answer and the trace are deliberately
 * excluded, since a four word title does not need them and they would dominate
 * the input cost.
 */
export interface TitleRequest {
  question: string;
}

export interface TitleResponse {
  /** Empty when generation failed, meaning keep the locally derived title. */
  title: string;
}

/* ------------------------------------------------------------------ */
/* POST /api/feedback                                                  */
/* ------------------------------------------------------------------ */

/**
 * Everything except `comment` is context the client already holds from the
 * answer being reported on, so the reviewer gets the full picture without the
 * user retyping anything.
 *
 * `userName` is self reported today. Once CCHMC SSO is in place the backend will
 * take the user from the authenticated session and this field goes away, along
 * with the name input in FeedbackDialog and the helpers in feedback.ts.
 */
export interface FeedbackRequest {
  /** Required, non blank, at most 8000 characters. */
  comment: string;
  userName: string;
  question: string;
  answer: string;
  mode: QueryMode | null;
  intent: string | null;
  skill: string | null;
  /** Whole trace plus timings, question type, agent, session, and any Cypher. */
  traceSnapshot: Record<string, unknown> | null;
}

export interface FeedbackResponse {
  ok: boolean;
  id: number;
}

/* ------------------------------------------------------------------ */
/* GET /api/admin/feedback                                             */
/* ------------------------------------------------------------------ */

/** One stored submission as the admin view reads it back. */
export interface FeedbackItem {
  id: number;
  /** Empty when the submitter stayed anonymous. */
  userName: string;
  question: string;
  answer: string;
  mode: string | null;
  intent: string | null;
  skill: string | null;
  comment: string;
  traceSnapshot: Record<string, unknown> | null;
  /** ISO 8601 with an offset, for example 2026-07-25T03:09:26.207408+00:00. */
  createdAt: string;
}

export interface FeedbackListResponse {
  /** Newest first, as ordered by the backend. */
  items: FeedbackItem[];
  /** Rows in the table, not rows in this page. */
  total: number;
}

/* ------------------------------------------------------------------ */
/* Server sent event payloads                                          */
/* ------------------------------------------------------------------ */

export interface StageProgress {
  done: number;
  total: number;
}

/** Payload of the `stage` event. */
export interface StageEvent {
  stage: string;
  label: string;
  detail?: string | null;
  progress?: StageProgress | null;
}

export interface RankedFaculty {
  name: string;
  score: number | null;
}

/** Payload of the `trace` event. Partial information for live display. */
export interface TraceEvent {
  ranked: RankedFaculty[];
}

/** Payload of the `error` event. */
export interface StreamErrorEvent {
  message: string;
}

/** Callbacks the SSE client invokes as events arrive. */
export interface StreamHandlers {
  onStage?: (event: StageEvent) => void;
  onTrace?: (event: TraceEvent) => void;
  onResult?: (event: QueryResponse) => void;
  onError?: (event: StreamErrorEvent) => void;
  onDone?: () => void;
}

/* ------------------------------------------------------------------ */
/* Client side view models                                             */
/* ------------------------------------------------------------------ */

/** Known pipeline stage identifiers, used for labels and icons. */
export type StageId =
  | 'classify'
  | 'route'
  | 'retrieve'
  | 'judge'
  | 'rank'
  | 'extract'
  | 'cypher_generate'
  | 'cypher_execute'
  | 'graph_query'
  | 'answer';

/** A stage as tracked by the UI while a query streams. */
export interface LiveStage {
  stage: string;
  label: string;
  detail: string | null;
  progress: StageProgress | null;
  status: 'active' | 'complete';
  startedAt: number;
  ms: number | null;
}

export type MessageRole = 'user' | 'assistant' | 'error';

export interface ChatMessage {
  id: string;
  role: MessageRole;
  createdAt: number;
  /** Present on user messages and on error messages. */
  text: string | null;
  /** Present on assistant messages once the result arrives. */
  response: QueryResponse | null;
  /** Mode the question was asked in, kept for the header of the message. */
  mode: QueryMode | null;
  /** Stages observed while this message streamed, retained after completion. */
  stages: LiveStage[];
  /** Live ranking snapshots, retained for context. */
  ranked: RankedFaculty[];
  /** True while the assistant message is still streaming. */
  pending: boolean;
}
