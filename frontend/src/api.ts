import type {
  HealthResponse,
  MetaResponse,
  QueryRequest,
  QueryResponse,
  StageEvent,
  StreamErrorEvent,
  StreamHandlers,
  TraceEvent,
} from './types';

/**
 * All requests use relative paths under /api. The Vite dev server proxies them
 * to http://localhost:8000 and nginx does the same in production, so no base
 * URL or environment variable is ever needed.
 */
const API_BASE = '/api';

/** Error carrying the HTTP status when the backend responds with a failure. */
export class ApiError extends Error {
  readonly status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
  }
}

/** True when a rejection came from an AbortController rather than a failure. */
export function isAbortError(error: unknown): boolean {
  return error instanceof DOMException && error.name === 'AbortError';
}

/** Turn any thrown value into a message safe to render. */
export function describeError(error: unknown): string {
  if (error instanceof ApiError) {
    return error.message;
  }
  if (error instanceof Error) {
    if (error.message === 'Failed to fetch' || error.message === 'Load failed') {
      return 'Could not reach the server. Check that the backend is running on port 8011.';
    }
    return error.message;
  }
  if (typeof error === 'string' && error.trim().length > 0) {
    return error;
  }
  return 'An unexpected error occurred.';
}

/** Read a hopefully useful message out of a non 2xx response body. */
async function readErrorBody(response: Response): Promise<string> {
  let raw = '';
  try {
    raw = await response.text();
  } catch {
    raw = '';
  }
  if (raw.length === 0) {
    return `Request failed with status ${response.status}.`;
  }
  try {
    const parsed: unknown = JSON.parse(raw);
    if (parsed && typeof parsed === 'object') {
      const record = parsed as Record<string, unknown>;
      for (const key of ['message', 'detail', 'error']) {
        const value = record[key];
        if (typeof value === 'string' && value.trim().length > 0) {
          return value;
        }
      }
    }
  } catch {
    // Body was not JSON, fall through and use the raw text.
  }
  return raw.slice(0, 400);
}

async function getJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    method: 'GET',
    headers: { Accept: 'application/json' },
    ...(signal ? { signal } : {}),
  });
  if (!response.ok) {
    throw new ApiError(await readErrorBody(response), response.status);
  }
  return (await response.json()) as T;
}

/** GET /api/health */
export function fetchHealth(signal?: AbortSignal): Promise<HealthResponse> {
  return getJson<HealthResponse>('/health', signal);
}

/** GET /api/meta */
export function fetchMeta(signal?: AbortSignal): Promise<MetaResponse> {
  return getJson<MetaResponse>('/meta', signal);
}

/** POST /api/query, the non streaming fallback. */
export async function postQuery(
  body: QueryRequest,
  signal?: AbortSignal,
): Promise<QueryResponse> {
  const response = await fetch(`${API_BASE}/query`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Accept: 'application/json',
    },
    body: JSON.stringify(body),
    ...(signal ? { signal } : {}),
  });
  if (!response.ok) {
    throw new ApiError(await readErrorBody(response), response.status);
  }
  return (await response.json()) as QueryResponse;
}

/* ------------------------------------------------------------------ */
/* Server sent event parsing                                           */
/* ------------------------------------------------------------------ */

interface ParsedEvent {
  name: string;
  data: string;
}

/**
 * Parse one server sent event block, that is the text between blank lines.
 * Handles `event:`, `data:` (possibly repeated, joined with newlines), and
 * ignores comment lines starting with a colon plus unknown fields.
 */
function parseEventBlock(block: string): ParsedEvent | null {
  const lines = block.split('\n');
  let name = 'message';
  const dataLines: string[] = [];

  for (const rawLine of lines) {
    // Strip a single trailing carriage return from CRLF delimited streams.
    const line = rawLine.endsWith('\r') ? rawLine.slice(0, -1) : rawLine;
    if (line.length === 0 || line.startsWith(':')) {
      continue;
    }
    const colonAt = line.indexOf(':');
    const field = colonAt === -1 ? line : line.slice(0, colonAt);
    let value = colonAt === -1 ? '' : line.slice(colonAt + 1);
    // A single leading space after the colon is part of the framing.
    if (value.startsWith(' ')) {
      value = value.slice(1);
    }
    if (field === 'event') {
      name = value;
    } else if (field === 'data') {
      dataLines.push(value);
    }
    // `id` and `retry` are not used by this API.
  }

  if (dataLines.length === 0) {
    return null;
  }
  return { name, data: dataLines.join('\n') };
}

function safeParseJson(raw: string): unknown {
  try {
    return JSON.parse(raw) as unknown;
  } catch {
    return null;
  }
}

/**
 * POST /api/query/stream and dispatch each named event to the handlers.
 *
 * EventSource cannot be used because the endpoint requires a POST body, so the
 * stream is read from the fetch response body and framed by hand. Partial
 * chunks are buffered until a blank line completes an event.
 *
 * Resolves when the stream finishes. Cancellation via the signal resolves
 * quietly rather than throwing, since a user pressing Stop is not an error.
 */
export async function streamQuery(
  body: QueryRequest,
  handlers: StreamHandlers,
  signal?: AbortSignal,
): Promise<void> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE}/query/stream`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Accept: 'text/event-stream',
        'Cache-Control': 'no-cache',
      },
      body: JSON.stringify(body),
      ...(signal ? { signal } : {}),
    });
  } catch (error) {
    if (isAbortError(error)) {
      return;
    }
    throw error;
  }

  if (!response.ok) {
    throw new ApiError(await readErrorBody(response), response.status);
  }
  if (!response.body) {
    throw new ApiError('The server returned an empty stream.', response.status);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder('utf-8');
  let buffer = '';
  let sawDone = false;

  const dispatch = (event: ParsedEvent): void => {
    const payload = safeParseJson(event.data);
    switch (event.name) {
      case 'stage': {
        if (payload && typeof payload === 'object' && handlers.onStage) {
          handlers.onStage(payload as StageEvent);
        }
        return;
      }
      case 'trace': {
        if (payload && typeof payload === 'object' && handlers.onTrace) {
          handlers.onTrace(payload as TraceEvent);
        }
        return;
      }
      case 'result': {
        if (payload && typeof payload === 'object' && handlers.onResult) {
          handlers.onResult(payload as QueryResponse);
        }
        return;
      }
      case 'error': {
        if (handlers.onError) {
          const record =
            payload && typeof payload === 'object'
              ? (payload as Record<string, unknown>)
              : {};
          const message =
            typeof record['message'] === 'string' && record['message'].length > 0
              ? record['message']
              : 'The server reported an error while answering.';
          handlers.onError({ message } as StreamErrorEvent);
        }
        return;
      }
      case 'done': {
        sawDone = true;
        if (handlers.onDone) {
          handlers.onDone();
        }
        return;
      }
      default:
        // Unknown event names are ignored so the backend can add events freely.
        return;
    }
  };

  /** Consume every complete event sitting in the buffer. */
  const drain = (): void => {
    // Events are separated by a blank line, which is \n\n or \r\n\r\n.
    for (;;) {
      const lf = buffer.indexOf('\n\n');
      const crlf = buffer.indexOf('\r\n\r\n');
      let cut = -1;
      let width = 0;
      if (lf !== -1 && (crlf === -1 || lf <= crlf)) {
        cut = lf;
        width = 2;
      } else if (crlf !== -1) {
        cut = crlf;
        width = 4;
      }
      if (cut === -1) {
        return;
      }
      const block = buffer.slice(0, cut);
      buffer = buffer.slice(cut + width);
      const event = parseEventBlock(block);
      if (event) {
        dispatch(event);
      }
    }
  };

  try {
    for (;;) {
      const { value, done } = await reader.read();
      if (done) {
        break;
      }
      buffer += decoder.decode(value, { stream: true });
      drain();
    }
    // Flush any trailing bytes and handle a final event with no blank line.
    buffer += decoder.decode();
    drain();
    const tail = buffer.trim();
    if (tail.length > 0) {
      const event = parseEventBlock(tail);
      if (event) {
        dispatch(event);
      }
    }
  } catch (error) {
    if (isAbortError(error)) {
      return;
    }
    throw error;
  } finally {
    try {
      reader.releaseLock();
    } catch {
      // Already released, nothing to do.
    }
  }

  // Some servers close without a done event, so make sure callers finish up.
  if (!sawDone && handlers.onDone) {
    handlers.onDone();
  }
}
