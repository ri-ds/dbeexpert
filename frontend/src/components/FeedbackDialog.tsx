import { useCallback, useId, useRef, useState } from 'react';
import { ApiError, describeError, isAbortError, submitFeedback } from '../api';
import {
  buildFeedbackRequest,
  COMMENT_MAX,
  readStoredName,
  writeStoredName,
  type FeedbackContext,
} from '../feedback';
import { useDialog } from '../hooks/useDialog';
import { ChevronIcon, CloseIcon } from './Icons';

/** Character count at which the counter starts warning about the ceiling. */
const COMMENT_WARN_AT = COMMENT_MAX - 500;

/** Longest attached value shown in the preview, which is a summary not an editor. */
const PREVIEW_MAX = 4000;

function preview(value: string): string {
  return value.length <= PREVIEW_MAX ? value : `${value.slice(0, PREVIEW_MAX)}\n[...]`;
}

/** Pretty print the snapshot, and never let a serialisation problem take the dialog down. */
function formatJson(value: unknown): string {
  try {
    return JSON.stringify(value, null, 2) ?? 'null';
  } catch {
    return 'This context could not be displayed, but it will still be sent.';
  }
}

export interface FeedbackDialogProps {
  /** Question, answer, and technical context attached to the submission. */
  context: FeedbackContext;
  /**
   * Display name of the signed in user, when the service provider told us. When
   * set, the name field is hidden: the backend takes the name from the session
   * and ignores anything the client sends, so asking for it would be misleading.
   */
  signedInAs?: string | null;
  onClose: () => void;
  onSubmitted: () => void;
}

/**
 * Feedback form for one answer.
 *
 * The user writes a comment and nothing else. Everything the reviewer needs to
 * reproduce the answer is attached automatically, and the "What gets sent"
 * disclosure shows exactly what that is, because asking someone to submit a
 * hidden payload is not reasonable.
 *
 * Same keyboard contract as every other dialog, which lives in useDialog.
 * Closing is blocked while a submission is in flight so a confirmation is never
 * lost, and a failure keeps the dialog open with the text intact.
 */
export default function FeedbackDialog({
  context,
  signedInAs = null,
  onClose,
  onSubmitted,
}: FeedbackDialogProps) {
  const [comment, setComment] = useState('');
  // Temporary, remove with the name field once CCHMC SSO fills the user in.
  const [name, setName] = useState<string>(() => readStoredName());
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const commentRef = useRef<HTMLTextAreaElement | null>(null);
  const panelRef = useDialog({
    onClose,
    initialFocusRef: commentRef,
    dismissible: !pending,
  });

  const titleId = useId();
  const commentId = useId();
  const countId = useId();
  const nameId = useId();
  const nameHintId = useId();
  const errorId = useId();

  const canSubmit = comment.trim().length > 0 && !pending;

  const submit = useCallback(() => {
    const body = buildFeedbackRequest(context, comment, name);
    if (body.comment.length === 0 || pending) {
      return;
    }

    setPending(true);
    setError(null);

    void submitFeedback(body)
      .then(() => {
        // Remembered only after a submission the backend accepted.
        writeStoredName(name);
        onSubmitted();
      })
      .catch((failure: unknown) => {
        if (isAbortError(failure)) {
          return;
        }
        setPending(false);
        // A 503 means the feedback database is unreachable. The answer itself is
        // untouched, and saying so keeps the message from reading as alarming.
        if (failure instanceof ApiError && failure.status === 503) {
          setError(
            'Your feedback could not be saved because the feedback database is not reachable right now. The answer itself is unaffected. Please try again in a few minutes.',
          );
          return;
        }
        setError(describeError(failure));
      });
  }, [comment, context, name, onSubmitted, pending]);

  const commentLength = comment.length;
  const nearLimit = commentLength >= COMMENT_WARN_AT;

  const attached: Array<{ key: string; value: string }> = [
    { key: 'Search mode', value: context.mode ?? 'not recorded' },
    { key: 'Intent', value: context.intent ?? 'not recorded' },
    { key: 'Skill', value: context.skill ?? 'not recorded' },
  ];

  return (
    <>
      <div
        className="backdrop"
        onClick={pending ? undefined : onClose}
        aria-hidden="true"
      />
      <div
        className="modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        ref={panelRef}
        tabIndex={-1}
      >
        <header className="modal__head">
          <h2 className="modal__title" id={titleId}>
            Send feedback on this answer
          </h2>
          <button
            type="button"
            className="icon-btn"
            onClick={onClose}
            disabled={pending}
            aria-label="Close the feedback form"
          >
            <CloseIcon size={16} />
          </button>
        </header>

        <form
          className="modal__form"
          onSubmit={(event) => {
            event.preventDefault();
            if (canSubmit) {
              submit();
            }
          }}
        >
          <div className="modal__body">
            <p className="modal__lead">
              Tell us what is wrong, missing, or good about this answer. The question, the
              answer, and the technical details of how it was produced are attached
              automatically, so there is nothing to copy across.
            </p>

            <details className="fb-attached">
              <summary className="fb-attached__summary">
                <span className="fb-attached__chev" aria-hidden="true">
                  <ChevronIcon size={13} />
                </span>
                <span>What gets sent</span>
              </summary>

              <div className="fb-attached__body">
                <div className="fb-attached__block">
                  <h3 className="fb-attached__label">Question</h3>
                  <p className="fb-attached__text">
                    {context.question.length > 0
                      ? preview(context.question)
                      : 'No question was recorded for this answer.'}
                  </p>
                </div>

                <div className="fb-attached__block">
                  <h3 className="fb-attached__label">Answer</h3>
                  <p className="fb-attached__text">{preview(context.answer)}</p>
                </div>

                <dl className="fb-attached__facts">
                  {attached.map((fact) => (
                    <div key={fact.key} className="fb-attached__fact">
                      <dt>{fact.key}</dt>
                      <dd>{fact.value}</dd>
                    </div>
                  ))}
                </dl>

                <div className="fb-attached__block">
                  <h3 className="fb-attached__label">Technical details</h3>
                  <div className="json-scroll">
                    <pre className="json-pre">{formatJson(context.traceSnapshot)}</pre>
                  </div>
                </div>
              </div>
            </details>

            <div className="field">
              <label className="field__label" htmlFor={commentId}>
                Your feedback
                <span className="field__flag field__flag--required">required</span>
              </label>
              <textarea
                id={commentId}
                ref={commentRef}
                className="field__area"
                value={comment}
                onChange={(event) => setComment(event.target.value)}
                maxLength={COMMENT_MAX}
                rows={5}
                spellCheck
                disabled={pending}
                placeholder="For example: the second person listed does not work on this topic."
                aria-describedby={error === null ? countId : `${countId} ${errorId}`}
              />
              <p
                className={`field__count${nearLimit ? ' is-near-limit' : ''}`}
                id={countId}
              >
                {commentLength > 0
                  ? `${commentLength.toLocaleString('en-US')} of ${COMMENT_MAX.toLocaleString('en-US')} characters`
                  : `Up to ${COMMENT_MAX.toLocaleString('en-US')} characters`}
              </p>
            </div>

            {/* The name field only exists for the anonymous case. When the
                service provider forwards an identity the backend takes the name
                from the session and ignores anything sent here, so showing the
                field would imply a choice the user does not have. */}
            {signedInAs ? (
              <p className="field__hint">
                Submitting as <strong>{signedInAs}</strong>, taken from your CCHMC sign in.
              </p>
            ) : (
              <div className="field">
                <label className="field__label" htmlFor={nameId}>
                  Your name
                  <span className="field__flag">optional</span>
                </label>
                <input
                  id={nameId}
                  className="field__input"
                  type="text"
                  value={name}
                  onChange={(event) => setName(event.target.value)}
                  maxLength={200}
                  autoComplete="name"
                  disabled={pending}
                  aria-describedby={nameHintId}
                />
                <p className="field__hint" id={nameHintId}>
                  Only so we can follow up with you. Leave it blank to stay anonymous. Your
                  name is remembered in this browser for next time.
                </p>
              </div>
            )}

            {error !== null ? (
              <p className="fb-error" id={errorId} role="alert">
                {error}
              </p>
            ) : null}
          </div>

          <footer className="modal__foot">
            <button
              type="button"
              className="btn btn--ghost"
              onClick={onClose}
              disabled={pending}
            >
              Cancel
            </button>
            <button type="submit" className="btn" disabled={!canSubmit}>
              {pending ? 'Sending' : 'Send feedback'}
            </button>
          </footer>
        </form>
      </div>
    </>
  );
}
