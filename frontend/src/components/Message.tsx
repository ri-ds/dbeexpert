import { useCallback, useMemo, useRef, useState } from 'react';
import { buildFeedbackContext } from '../feedback';
import type { ChatMessage } from '../types';
import CypherBlock from './CypherBlock';
import FacultyCard from './FacultyCard';
import FeedbackDialog from './FeedbackDialog';
import { AlertIcon, CheckIcon, FeedbackIcon, PeopleIcon, SparkIcon } from './Icons';
import PipelineTrace from './PipelineTrace';
import ResultTable from './ResultTable';
import StageIndicator from './StageIndicator';

/** Short local time, for example 2:41 PM. */
export function formatTime(timestamp: number): string {
  try {
    return new Date(timestamp).toLocaleTimeString('en-US', {
      hour: 'numeric',
      minute: '2-digit',
    });
  } catch {
    return '';
  }
}

export interface MessageProps {
  message: ChatMessage;
  /** Elapsed milliseconds, only meaningful while the message is pending. */
  elapsedMs: number;
  /**
   * Text of the user message this answer replies to. The message itself does not
   * carry the question, and feedback is useless without it.
   */
  question?: string | null;
  /** Display name of the signed in user, when the SP told us. */
  signedInAs?: string | null;
}

/**
 * Dispatch on role. The assistant branch is a separate component because an
 * assistant message can be turned into an error message in place, keeping its
 * id, and that has to unmount the assistant subtree rather than change which
 * hooks run for the same instance.
 */
export default function Message({
  message,
  elapsedMs,
  question = null,
  signedInAs = null,
}: MessageProps) {
  if (message.role === 'user') {
    return (
      <article className="msg msg--user" aria-label="Your question">
        <div className="msg__bubble">{message.text}</div>
        <div className="msg__meta">
          <span>You</span>
          <span className="msg__dot" aria-hidden="true" />
          <time dateTime={new Date(message.createdAt).toISOString()}>
            {formatTime(message.createdAt)}
          </time>
        </div>
      </article>
    );
  }

  if (message.role === 'error') {
    return (
      <article className="msg msg--error" aria-label="Error">
        <div className="msg__error">
          <span className="msg__error-icon" aria-hidden="true">
            <AlertIcon size={16} />
          </span>
          <div className="msg__error-body">
            <p className="msg__error-title">Something went wrong</p>
            <p className="msg__error-text">{message.text}</p>
          </div>
        </div>
        <div className="msg__meta">
          <time dateTime={new Date(message.createdAt).toISOString()}>
            {formatTime(message.createdAt)}
          </time>
        </div>
      </article>
    );
  }

  return (
    <AssistantMessage
      message={message}
      elapsedMs={elapsedMs}
      question={question}
      signedInAs={signedInAs}
    />
  );
}

interface AssistantMessageProps {
  message: ChatMessage;
  elapsedMs: number;
  question: string | null;
  signedInAs: string | null;
}

function AssistantMessage({
  message,
  elapsedMs,
  question,
  signedInAs,
}: AssistantMessageProps) {
  const response = message.response;
  const faculty = response?.faculty ?? [];
  const cypher = response?.cypher ?? null;
  const answerText = response?.answerText ?? null;

  const [feedbackOpen, setFeedbackOpen] = useState(false);
  const [feedbackSent, setFeedbackSent] = useState(false);
  const feedbackBtnRef = useRef<HTMLButtonElement | null>(null);

  // Feedback is only offered on a finished answer. A pending message has nothing
  // to report on yet, and a message with no response has nothing to attach.
  const canGiveFeedback = !message.pending && response !== null;

  const feedbackContext = useMemo(
    () => (feedbackOpen ? buildFeedbackContext(question, response) : null),
    [feedbackOpen, question, response],
  );

  const closeFeedback = useCallback(() => {
    setFeedbackOpen(false);
    // Return focus to the trigger, which the dialog cannot know about.
    window.setTimeout(() => feedbackBtnRef.current?.focus(), 0);
  }, []);

  const onFeedbackSubmitted = useCallback(() => {
    setFeedbackSent(true);
    closeFeedback();
  }, [closeFeedback]);

  return (
    <article className="msg msg--assistant" aria-label="Answer">
      <div className="msg__assistant-head">
        <span className="msg__avatar" aria-hidden="true">
          <SparkIcon size={14} />
        </span>
        <span className="msg__who">Expertise Explorer</span>
        {/* No mode, question type, or agent tags here. All of that is routing
            detail, and it is already available under "How this answer was
            produced" at the foot of the message. */}
        <time
          className="msg__time"
          dateTime={new Date(message.createdAt).toISOString()}
        >
          {formatTime(message.createdAt)}
        </time>
      </div>

      <div className="msg__body">
        {message.pending ? (
          <StageIndicator
            stages={message.stages}
            ranked={message.ranked}
            elapsedMs={elapsedMs}
          />
        ) : null}

        {!message.pending && answerText && answerText.trim().length > 0 ? (
          <div className="prose">
            {answerText
              .split(/\n{2,}/)
              .map((paragraph) => paragraph.trim())
              .filter((paragraph) => paragraph.length > 0)
              .map((paragraph, index) => (
                <p key={index}>{paragraph}</p>
              ))}
          </div>
        ) : null}

        {!message.pending && faculty.length > 0 ? (
          <>
            <div className="msg__section-head">
              <span aria-hidden="true">
                <PeopleIcon size={14} />
              </span>
              <span>
                {faculty.length} {faculty.length === 1 ? 'faculty match' : 'faculty matches'}
              </span>
            </div>
            <div className="faculty-list">
              {faculty.map((item, index) => (
                <FacultyCard
                  key={`${item.name}-${index}`}
                  faculty={item}
                  rank={index + 1}
                />
              ))}
            </div>
          </>
        ) : null}

        {!message.pending && cypher ? (
          <>
            <CypherBlock
              query={cypher.query}
              params={cypher.params}
              kind={cypher.kind}
              explanation={cypher.explanation}
            />
            {/* The table is suppressed when the prose answer already states the
                whole result, which is the case for single value queries. Showing
                both put the same data on screen twice. */}
            {cypher.showTable !== false ? (
              <ResultTable columns={cypher.columns ?? []} rows={cypher.rows ?? []} />
            ) : null}
          </>
        ) : null}

        {!message.pending &&
        faculty.length === 0 &&
        cypher === null &&
        (answerText === null || answerText.trim().length === 0) ? (
          <p className="msg__nothing">
            No matching faculty were found for that question. Try widening the wording,
            or switch to a different search mode.
          </p>
        ) : null}

        {/* Footer row: how the answer was produced on the left, feedback on the
            right. The disclosure keeps its own collapsed by default behaviour. */}
        {!message.pending ? (
          <div className="msg__foot">
            <PipelineTrace
              trace={response?.trace ?? null}
              timings={response?.timings ?? null}
              fallbackStages={message.stages}
              intent={response?.intent ?? null}
            />
            {canGiveFeedback ? (
              <button
                type="button"
                className="msg__feedback"
                ref={feedbackBtnRef}
                onClick={() => setFeedbackOpen(true)}
                aria-haspopup="dialog"
                aria-expanded={feedbackOpen}
              >
                <FeedbackIcon size={13} />
                <span>Feedback</span>
              </button>
            ) : null}
            {feedbackSent ? (
              <p className="msg__feedback-note" role="status">
                <span aria-hidden="true">
                  <CheckIcon size={13} />
                </span>
                <span>Feedback sent. Thank you, it has been saved for review.</span>
              </p>
            ) : null}
          </div>
        ) : null}
      </div>

      {feedbackOpen && feedbackContext !== null ? (
        <FeedbackDialog
          context={feedbackContext}
          signedInAs={signedInAs}
          onClose={closeFeedback}
          onSubmitted={onFeedbackSubmitted}
        />
      ) : null}
    </article>
  );
}
