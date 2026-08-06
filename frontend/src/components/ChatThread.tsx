import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import type { ChatMessage } from '../types';
import EmptyState from './EmptyState';
import { ChevronIcon } from './Icons';
import Message from './Message';

/** Distance from the bottom, in pixels, still treated as "at the bottom". */
const STICK_THRESHOLD = 96;

/**
 * Text of the nearest user message above the given position, which is the
 * question an assistant message answers. Searching backwards rather than
 * assuming index minus one holds even if an error message sits between them.
 */
function precedingQuestion(messages: ChatMessage[], index: number): string | null {
  for (let at = index - 1; at >= 0; at -= 1) {
    const candidate = messages[at];
    if (candidate === undefined) {
      continue;
    }
    if (candidate.role === 'user') {
      return candidate.text;
    }
  }
  return null;
}

export interface ChatThreadProps {
  messages: ChatMessage[];
  facultyCount: number;
  onPickExample: (question: string) => void;
  /** Display name of the signed in user, forwarded to the feedback form. */
  signedInAs?: string | null;
  /** Ticks while a request is in flight so pending timers stay live. */
  elapsedMs: number;
}

/**
 * The scrolling conversation. Auto scrolls to the newest content, but only
 * while the reader is already near the bottom, so scrolling up to re read an
 * earlier answer is never interrupted.
 */
export default function ChatThread({
  messages,
  facultyCount,
  onPickExample,
  elapsedMs,
  signedInAs = null,
}: ChatThreadProps) {
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const stickRef = useRef<boolean>(true);
  const [showJump, setShowJump] = useState(false);

  const scrollToBottom = useCallback((smooth: boolean) => {
    const node = scrollRef.current;
    if (!node) {
      return;
    }
    node.scrollTo({ top: node.scrollHeight, behavior: smooth ? 'smooth' : 'auto' });
  }, []);

  const onScroll = useCallback(() => {
    const node = scrollRef.current;
    if (!node) {
      return;
    }
    const distance = node.scrollHeight - node.scrollTop - node.clientHeight;
    const atBottom = distance <= STICK_THRESHOLD;
    stickRef.current = atBottom;
    setShowJump(!atBottom && node.scrollHeight > node.clientHeight + 200);
  }, []);

  // New messages: follow along only if the reader had not scrolled away.
  useLayoutEffect(() => {
    if (stickRef.current) {
      scrollToBottom(messages.length > 1);
    }
  }, [messages, scrollToBottom]);

  // Streaming growth: keep the live stage list in view without yanking.
  useEffect(() => {
    if (stickRef.current) {
      scrollToBottom(false);
    }
  }, [elapsedMs, scrollToBottom]);

  const isEmpty = messages.length === 0;

  return (
    <div className="thread-wrap">
      <div
        className="thread"
        ref={scrollRef}
        onScroll={onScroll}
        tabIndex={-1}
        aria-label="Conversation"
      >
        <div className="thread__inner">
          {isEmpty ? (
            <EmptyState facultyCount={facultyCount} onPick={onPickExample} />
          ) : (
            messages.map((message, index) => (
              <Message
                key={message.id}
                message={message}
                elapsedMs={elapsedMs}
                // The thread is a flat list of alternating messages, so the
                // question an answer replies to is the nearest user message
                // above it. Only the thread knows that, so it is passed down.
                question={precedingQuestion(messages, index)}
                signedInAs={signedInAs}
              />
            ))
          )}
          <div className="thread__tail" aria-hidden="true" />
        </div>
      </div>

      {showJump ? (
        <button
          type="button"
          className="jump-btn"
          onClick={() => {
            stickRef.current = true;
            setShowJump(false);
            scrollToBottom(true);
          }}
          aria-label="Scroll to the newest message"
        >
          <span className="jump-btn__icon" aria-hidden="true">
            <ChevronIcon size={14} />
          </span>
          <span>Newest</span>
        </button>
      ) : null}
    </div>
  );
}
