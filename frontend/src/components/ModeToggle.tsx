import { useCallback, useId, useRef } from 'react';
import type { KeyboardEvent as ReactKeyboardEvent } from 'react';
import type { ModeInfo, QueryMode } from '../types';

/**
 * Compact wording so three modes fit in the composer. The full label is still
 * the accessible name, and the description is on the tooltip.
 */
const SHORT_LABELS: Record<string, string> = {
  hybrid: 'Hybrid',
  vector: 'Vector',
  cypher: 'Cypher',
};

function shortLabel(mode: ModeInfo): string {
  const known = SHORT_LABELS[mode.id];
  if (known !== undefined) {
    return known;
  }
  const first = mode.label.trim().split(/\s+/)[0];
  return first !== undefined && first.length > 0 ? first : mode.id;
}

export interface ModeToggleProps {
  modes: ModeInfo[];
  value: QueryMode;
  onChange: (next: QueryMode) => void;
  disabled?: boolean;
}

/**
 * Segmented search mode control that lives under the send button. Implemented
 * as a real radio group: one tab stop, arrow keys move and select, Home and End
 * jump to the ends, and the selected segment carries aria-checked.
 */
export default function ModeToggle({
  modes,
  value,
  onChange,
  disabled = false,
}: ModeToggleProps) {
  const refs = useRef<Array<HTMLButtonElement | null>>([]);
  const labelId = useId();

  const focusAndSelect = useCallback(
    (index: number) => {
      const target = modes[index];
      if (!target) {
        return;
      }
      onChange(target.id);
      refs.current[index]?.focus();
    },
    [modes, onChange],
  );

  const onKeyDown = useCallback(
    (event: ReactKeyboardEvent<HTMLDivElement>) => {
      if (disabled || modes.length === 0) {
        return;
      }
      const current = modes.findIndex((mode) => mode.id === value);
      const index = current === -1 ? 0 : current;

      switch (event.key) {
        case 'ArrowRight':
        case 'ArrowDown':
          event.preventDefault();
          focusAndSelect((index + 1) % modes.length);
          return;
        case 'ArrowLeft':
        case 'ArrowUp':
          event.preventDefault();
          focusAndSelect((index - 1 + modes.length) % modes.length);
          return;
        case 'Home':
          event.preventDefault();
          focusAndSelect(0);
          return;
        case 'End':
          event.preventDefault();
          focusAndSelect(modes.length - 1);
          return;
        default:
          return;
      }
    },
    [disabled, focusAndSelect, modes, value],
  );

  if (modes.length === 0) {
    return null;
  }

  const activeIndex = Math.max(
    0,
    modes.findIndex((mode) => mode.id === value),
  );

  return (
    <div className="mode-toggle">
      <span className="mode-toggle__label" id={labelId}>
        Search mode
      </span>
      <div
        className="mode-toggle__group"
        role="radiogroup"
        aria-labelledby={labelId}
        onKeyDown={onKeyDown}
      >
        {modes.map((mode, index) => {
          const selected = mode.id === value;
          return (
            <button
              key={mode.id}
              type="button"
              role="radio"
              aria-checked={selected}
              aria-label={mode.label}
              title={`${mode.label}. ${mode.description}`}
              tabIndex={index === activeIndex ? 0 : -1}
              disabled={disabled}
              ref={(node) => {
                refs.current[index] = node;
              }}
              className={`mode-toggle__opt${selected ? ' is-selected' : ''}`}
              onClick={() => onChange(mode.id)}
            >
              <span className="mode-toggle__dot" aria-hidden="true" />
              <span>{shortLabel(mode)}</span>
            </button>
          );
        })}
      </div>
    </div>
  );
}
