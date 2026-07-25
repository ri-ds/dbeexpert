import { forwardRef, useCallback, useEffect, useImperativeHandle, useRef } from 'react';
import type { ChangeEvent, KeyboardEvent as ReactKeyboardEvent } from 'react';
import type { ModeInfo, QueryMode } from '../types';
import { SendIcon, StopIcon } from './Icons';
import ModeToggle from './ModeToggle';

/** Maximum height before the textarea starts scrolling instead of growing. */
const MAX_HEIGHT = 200;

export interface ComposerHandle {
  focus: () => void;
}

export interface ComposerProps {
  value: string;
  onChange: (next: string) => void;
  onSubmit: () => void;
  onStop: () => void;
  busy: boolean;
  /** Disables sending entirely, for example when the backend is unreachable. */
  blocked?: boolean;
  modes: ModeInfo[];
  mode: QueryMode;
  onModeChange: (next: QueryMode) => void;
}

/**
 * Question input. Grows with its content up to a ceiling, sends on Enter,
 * inserts a newline on Shift plus Enter, and swaps the send button for a Stop
 * button while a request is in flight.
 *
 * The search mode control sits directly under the send button, with the active
 * mode's description beside it so the choice is never opaque.
 */
const Composer = forwardRef<ComposerHandle, ComposerProps>(function Composer(
  { value, onChange, onSubmit, onStop, busy, blocked = false, modes, mode, onModeChange },
  ref,
) {
  const areaRef = useRef<HTMLTextAreaElement | null>(null);

  useImperativeHandle(ref, () => ({
    focus: () => {
      areaRef.current?.focus();
    },
  }));

  /** Reset the height, then match the content up to the ceiling. */
  const resize = useCallback(() => {
    const node = areaRef.current;
    if (!node) {
      return;
    }
    // A wrapped placeholder counts toward scrollHeight, which would make an
    // empty box several lines tall on narrow screens. Measure without it.
    const placeholder = node.placeholder;
    node.placeholder = '';
    node.style.height = 'auto';
    const content = node.scrollHeight;
    node.style.height = `${Math.min(content, MAX_HEIGHT)}px`;
    node.style.overflowY = content > MAX_HEIGHT ? 'auto' : 'hidden';
    node.placeholder = placeholder;
  }, []);

  useEffect(() => {
    resize();
  }, [resize, value]);

  // Re measure when the viewport changes, both because the wrap point moves and
  // so a size computed during an incomplete first layout cannot get stuck.
  useEffect(() => {
    window.addEventListener('resize', resize);
    return () => {
      window.removeEventListener('resize', resize);
    };
  }, [resize]);

  const canSend = !busy && !blocked && value.trim().length > 0;

  const onKeyDown = useCallback(
    (event: ReactKeyboardEvent<HTMLTextAreaElement>) => {
      if (event.key !== 'Enter' || event.shiftKey) {
        return;
      }
      // Do not send while an input method editor is composing.
      if (event.nativeEvent.isComposing) {
        return;
      }
      event.preventDefault();
      if (value.trim().length > 0 && !busy && !blocked) {
        onSubmit();
      }
    },
    [blocked, busy, onSubmit, value],
  );

  const onInput = useCallback(
    (event: ChangeEvent<HTMLTextAreaElement>) => {
      onChange(event.target.value);
    },
    [onChange],
  );

  const trimmed = value.trim().length;
  const activeMode = modes.find((item) => item.id === mode) ?? modes[0];

  return (
    <form
      className="composer"
      onSubmit={(event) => {
        event.preventDefault();
        if (canSend) {
          onSubmit();
        }
      }}
    >
      <div className={`composer__field${busy ? ' is-busy' : ''}`}>
        <label className="sr-only" htmlFor="composer-input">
          Ask a question about DBE faculty expertise
        </label>
        <textarea
          id="composer-input"
          ref={areaRef}
          className="composer__input"
          value={value}
          onChange={onInput}
          onKeyDown={onKeyDown}
          rows={1}
          disabled={busy || blocked}
          spellCheck
          autoComplete="off"
          placeholder={
            blocked ? 'Unavailable until the backend is reachable' : 'Ask about faculty expertise'
          }
          aria-describedby="composer-hint"
        />

        {busy ? (
          <button
            type="button"
            className="btn btn--stop"
            onClick={onStop}
            aria-label="Stop generating the answer"
          >
            <StopIcon size={13} />
            <span>Stop</span>
          </button>
        ) : (
          <button
            type="submit"
            className="btn btn--send"
            disabled={!canSend}
            aria-label="Send question"
          >
            <SendIcon size={15} />
            <span>Send</span>
          </button>
        )}
      </div>

      {modes.length > 0 ? (
        <div className="composer__modes">
          {activeMode ? (
            <p className="composer__mode-desc">{activeMode.description}</p>
          ) : null}
          <ModeToggle
            modes={modes}
            value={mode}
            onChange={onModeChange}
            disabled={busy}
          />
        </div>
      ) : null}

      <p className="composer__hint" id="composer-hint">
        <span>
          <kbd>Enter</kbd> to send, <kbd>Shift</kbd> plus <kbd>Enter</kbd> for a new line
        </span>
        {trimmed > 0 ? (
          <span className="composer__count">
            {trimmed.toLocaleString('en-US')} characters
          </span>
        ) : null}
      </p>
    </form>
  );
});

export default Composer;
