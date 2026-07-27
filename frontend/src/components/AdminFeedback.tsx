import { useCallback, useEffect, useId, useRef, useState } from 'react';
import { ApiError, describeError, fetchAdminFeedback, isAbortError } from '../api';
import { useTheme } from '../hooks/useTheme';
import type { FeedbackItem } from '../types';
import { AlertIcon, ChevronIcon } from './Icons';

/**
 * Temporary read back of submitted feedback, reached at /admin.
 *
 * Gated on the shared password the backend checks in the X-Admin-Password
 * header. That is adequate only because the whole page is a placeholder: when
 * CCHMC SSO lands this should be gated on a group claim and the password should
 * be deleted outright. The copy on the page says as much, so nobody mistakes it
 * for a finished admin area.
 *
 * Nothing here trusts the payload. Rows are normalized on the way in, because a
 * malformed or partly null row must render as a gap rather than a blank screen.
 */

/** Rows per page. Also the point at which paging controls appear. */
const PAGE_SIZE = 20;

/**
 * Session storage, never localStorage: the password must not outlive the tab.
 */
const PASSWORD_KEY = 'dbe.admin.password';

function readStoredPassword(): string {
  try {
    const stored = window.sessionStorage.getItem(PASSWORD_KEY);
    return typeof stored === 'string' ? stored : '';
  } catch {
    // Storage can be unavailable in private browsing modes.
    return '';
  }
}

function writeStoredPassword(password: string): void {
  try {
    window.sessionStorage.setItem(PASSWORD_KEY, password);
  } catch {
    // Persisting is best effort only, the password still works in memory.
  }
}

function clearStoredPassword(): void {
  try {
    window.sessionStorage.removeItem(PASSWORD_KEY);
  } catch {
    // Nothing to do, there is no store to clear.
  }
}

/* ------------------------------------------------------------------ */
/* Defensive parsing                                                   */
/* ------------------------------------------------------------------ */

function asRecord(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function asText(value: unknown): string {
  return typeof value === 'string' ? value : '';
}

function asOptionalText(value: unknown): string | null {
  return typeof value === 'string' && value.trim().length > 0 ? value : null;
}

function normalizeItem(value: unknown, index: number): FeedbackItem {
  const record = asRecord(value) ?? {};
  const id = record['id'];
  return {
    // A row with no id still has to render, and the key has to be unique.
    id: typeof id === 'number' && Number.isFinite(id) ? id : -1 - index,
    userName: asText(record['userName']),
    question: asText(record['question']),
    answer: asText(record['answer']),
    mode: asOptionalText(record['mode']),
    intent: asOptionalText(record['intent']),
    skill: asOptionalText(record['skill']),
    comment: asText(record['comment']),
    traceSnapshot: asRecord(record['traceSnapshot']),
    createdAt: asText(record['createdAt']),
  };
}

function normalizeList(value: unknown): { items: FeedbackItem[]; total: number } {
  const record = asRecord(value) ?? {};
  const raw = Array.isArray(record['items']) ? record['items'] : [];
  const total = record['total'];
  return {
    items: raw.map((entry, index) => normalizeItem(entry, index)),
    total: typeof total === 'number' && Number.isFinite(total) ? total : raw.length,
  };
}

/* ------------------------------------------------------------------ */
/* Formatting                                                          */
/* ------------------------------------------------------------------ */

/** Readable local date and time, falling back to the raw value it came as. */
function formatWhen(iso: string): string {
  if (iso.length === 0) {
    return 'Date not recorded';
  }
  const when = new Date(iso);
  if (Number.isNaN(when.getTime())) {
    return iso;
  }
  try {
    return when.toLocaleString('en-US', {
      year: 'numeric',
      month: 'short',
      day: 'numeric',
      hour: 'numeric',
      minute: '2-digit',
    });
  } catch {
    return iso;
  }
}

/** A valid datetime attribute, or nothing when the value cannot be parsed. */
function isoOrNull(iso: string): string | undefined {
  const when = new Date(iso);
  return Number.isNaN(when.getTime()) ? undefined : when.toISOString();
}

function formatJson(value: unknown): string {
  try {
    return JSON.stringify(value, null, 2) ?? 'null';
  } catch {
    return 'This snapshot could not be displayed.';
  }
}

/* ------------------------------------------------------------------ */
/* Page                                                                */
/* ------------------------------------------------------------------ */

export default function AdminFeedback() {
  // Keeps `system` theme live on this page too. The pre paint script in
  // index.html has already applied the stored choice by the time this runs.
  useTheme();

  const [password, setPassword] = useState<string>(() => readStoredPassword());
  const [draft, setDraft] = useState('');
  const [gateError, setGateError] = useState<string | null>(null);

  const [items, setItems] = useState<FeedbackItem[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  /** Bumped by the refresh button to re run the load with the same paging. */
  const [reloadAt, setReloadAt] = useState(0);

  const passwordRef = useRef<HTMLInputElement | null>(null);
  const passwordId = useId();

  const signedIn = password.length > 0;

  const logOut = useCallback(() => {
    clearStoredPassword();
    setPassword('');
    setDraft('');
    setItems([]);
    setTotal(0);
    setOffset(0);
    setLoadError(null);
    setGateError(null);
  }, []);

  useEffect(() => {
    if (!signedIn) {
      return;
    }
    const controller = new AbortController();
    setLoading(true);
    setLoadError(null);

    void (async () => {
      try {
        const payload = await fetchAdminFeedback(
          password,
          PAGE_SIZE,
          offset,
          controller.signal,
        );
        if (controller.signal.aborted) {
          return;
        }
        const normalized = normalizeList(payload);
        setItems(normalized.items);
        setTotal(normalized.total);
        // Only remember a password the backend has actually accepted.
        writeStoredPassword(password);
      } catch (error) {
        if (isAbortError(error) || controller.signal.aborted) {
          return;
        }
        if (error instanceof ApiError && error.status === 401) {
          // Discard a rejected password rather than retrying with it, in memory,
          // in the field, and in session storage.
          clearStoredPassword();
          setPassword('');
          setDraft('');
          setItems([]);
          setTotal(0);
          setOffset(0);
          setGateError('Incorrect password');
          return;
        }
        setLoadError(describeError(error));
      } finally {
        if (!controller.signal.aborted) {
          setLoading(false);
        }
      }
    })();

    return () => {
      controller.abort();
    };
  }, [offset, password, reloadAt, signedIn]);

  // Put focus back on the field when a password is rejected, so a correction
  // takes no clicks.
  useEffect(() => {
    if (gateError !== null) {
      passwordRef.current?.focus();
    }
  }, [gateError]);

  /* ---------------------------------------------------------------- */
  /* Password gate                                                     */
  /* ---------------------------------------------------------------- */

  if (!signedIn) {
    return (
      <div className="admin admin--gate">
        <form
          className="gate"
          onSubmit={(event) => {
            event.preventDefault();
            const next = draft.trim();
            if (next.length === 0) {
              return;
            }
            setGateError(null);
            setOffset(0);
            setPassword(next);
          }}
        >
          <h1 className="gate__title">Feedback submissions</h1>
          <p className="gate__lead">
            Temporary admin view for reading feedback submitted about answers. It is
            behind a single shared password and will be replaced by CCHMC sign in.
          </p>

          <div className="field">
            <label className="field__label" htmlFor={passwordId}>
              Admin password
            </label>
            <input
              id={passwordId}
              ref={passwordRef}
              className="field__input"
              type="password"
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              autoComplete="current-password"
              autoFocus
            />
          </div>

          {gateError !== null ? (
            <p className="gate__error" role="alert">
              <span aria-hidden="true">
                <AlertIcon size={15} />
              </span>
              <span>{gateError}</span>
            </p>
          ) : null}

          <button type="submit" className="btn btn--block" disabled={draft.trim().length === 0}>
            View submissions
          </button>
        </form>
      </div>
    );
  }

  /* ---------------------------------------------------------------- */
  /* List                                                              */
  /* ---------------------------------------------------------------- */

  const page = Math.floor(offset / PAGE_SIZE) + 1;
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const first = items.length === 0 ? 0 : offset + 1;
  const last = offset + items.length;
  const paged = total > PAGE_SIZE;

  return (
    <div className="admin">
      <header className="admin__top">
        <div className="admin__titles">
          <h1 className="admin__title">
            Feedback submissions
            <span className="admin__badge">Temporary view</span>
          </h1>
          <p className="admin__sub">
            {loading
              ? 'Loading submissions'
              : `${total.toLocaleString('en-US')} ${total === 1 ? 'submission' : 'submissions'} in total`}
          </p>
        </div>

        <div className="admin__actions">
          <button
            type="button"
            className="btn btn--ghost btn--sm"
            onClick={() => setReloadAt((current) => current + 1)}
            disabled={loading}
          >
            Refresh
          </button>
          <button type="button" className="btn btn--ghost btn--sm" onClick={logOut}>
            Log out
          </button>
        </div>
      </header>

      <div className="admin__scroll">
        <div className="admin__inner">
          <p className="admin__notice">
            This page is a placeholder until CCHMC sign in is added. It is shared password
            only, so treat anything here as visible to everyone who has that password.
          </p>

          {loadError !== null ? (
            <p className="gate__error" role="alert">
              <span aria-hidden="true">
                <AlertIcon size={15} />
              </span>
              <span>{loadError}</span>
            </p>
          ) : null}

          {!loading && loadError === null && items.length === 0 ? (
            <p className="admin__empty">
              No feedback has been submitted yet. Submissions appear here as soon as
              someone uses the Feedback button under an answer.
            </p>
          ) : null}

          {items.map((item) => (
            <article className="fbrow" key={item.id}>
              <header className="fbrow__head">
                <span className="fbrow__who">
                  {item.userName.trim().length > 0 ? item.userName : 'Anonymous'}
                </span>
                <time className="fbrow__when" dateTime={isoOrNull(item.createdAt)}>
                  {formatWhen(item.createdAt)}
                </time>
              </header>

              <p className="fbrow__comment">
                {item.comment.trim().length > 0 ? item.comment : 'No comment was recorded.'}
              </p>

              <div className="fbrow__block">
                <h2 className="fbrow__label">Question</h2>
                <p className="fbrow__text">
                  {item.question.trim().length > 0 ? item.question : 'Not recorded'}
                </p>
              </div>

              <div className="fbrow__block">
                <h2 className="fbrow__label">Answer</h2>
                <p className="fbrow__text fbrow__text--tall">
                  {item.answer.trim().length > 0 ? item.answer : 'Not recorded'}
                </p>
              </div>

              <ul className="fbrow__tags">
                <li className="fbrow__tag">
                  <span className="fbrow__tag-key">mode</span>
                  <span>{item.mode ?? 'none'}</span>
                </li>
                <li className="fbrow__tag">
                  <span className="fbrow__tag-key">intent</span>
                  <span>{item.intent ?? 'none'}</span>
                </li>
                <li className="fbrow__tag">
                  <span className="fbrow__tag-key">skill</span>
                  <span>{item.skill ?? 'none'}</span>
                </li>
              </ul>

              {item.traceSnapshot !== null ? (
                <details className="fb-attached">
                  <summary className="fb-attached__summary">
                    <span className="fb-attached__chev" aria-hidden="true">
                      <ChevronIcon size={13} />
                    </span>
                    <span>Technical details</span>
                  </summary>
                  <div className="fb-attached__body">
                    <div className="json-scroll">
                      <pre className="json-pre">{formatJson(item.traceSnapshot)}</pre>
                    </div>
                  </div>
                </details>
              ) : (
                <p className="fbrow__no-trace">No technical details were attached.</p>
              )}
            </article>
          ))}

          {paged ? (
            <nav className="admin__pager" aria-label="Pages of submissions">
              <button
                type="button"
                className="btn btn--ghost btn--sm"
                onClick={() => setOffset((current) => Math.max(0, current - PAGE_SIZE))}
                disabled={loading || offset === 0}
              >
                Previous
              </button>
              <span className="admin__pager-text">
                {`Showing ${first.toLocaleString('en-US')} to ${last.toLocaleString('en-US')} of ${total.toLocaleString('en-US')}, page ${page} of ${pages}`}
              </span>
              <button
                type="button"
                className="btn btn--ghost btn--sm"
                onClick={() => setOffset((current) => current + PAGE_SIZE)}
                disabled={loading || offset + PAGE_SIZE >= total}
              >
                Next
              </button>
            </nav>
          ) : null}
        </div>
      </div>
    </div>
  );
}
