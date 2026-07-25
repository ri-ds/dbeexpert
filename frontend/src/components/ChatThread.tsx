import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import type { ChatMessage } from '../types';
import EmptyState from './EmptyState';
import { ChevronIcon } from './Icons';
import Message from './Message';

/** Distance from the bottom, in pixels, still treated as "at the bottom". */
const STICK_THRESHOLD = 96;

export interface ChatThreadProps {
  messages: ChatMessage[];
  facultyCount: number;
  onPickExample: (question: string) => void;
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
            messages.map((message) => (
              <Message key={message.id} message={message} elapsedMs={elapsedMs} />
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
