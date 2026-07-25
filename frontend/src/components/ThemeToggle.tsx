import type { KeyboardEvent as ReactKeyboardEvent } from 'react';
import type { ResolvedTheme, ThemeMode } from '../hooks/useTheme';
import { MoonIcon, SunIcon, SystemIcon } from './Icons';

export interface ThemeToggleProps {
  mode: ThemeMode;
  resolved: ResolvedTheme;
  setMode: (next: ThemeMode) => void;
}

const OPTIONS: Array<{ id: ThemeMode; label: string }> = [
  { id: 'light', label: 'Light' },
  { id: 'dark', label: 'Dark' },
  { id: 'system', label: 'System' },
];

/**
 * Three way theme control. Rendered as a radio group so the whole thing is one
 * tab stop and arrow keys move between options.
 */
export default function ThemeToggle({ mode, resolved, setMode }: ThemeToggleProps) {
  const onKeyDown = (event: ReactKeyboardEvent<HTMLDivElement>): void => {
    const keys = ['ArrowRight', 'ArrowDown', 'ArrowLeft', 'ArrowUp'];
    if (!keys.includes(event.key)) {
      return;
    }
    event.preventDefault();
    const index = OPTIONS.findIndex((option) => option.id === mode);
    const step = event.key === 'ArrowRight' || event.key === 'ArrowDown' ? 1 : -1;
    const nextIndex = (index + step + OPTIONS.length) % OPTIONS.length;
    const next = OPTIONS[nextIndex];
    if (next) {
      setMode(next.id);
    }
  };

  return (
    <div className="theme-toggle">
      <span className="theme-toggle__label" id="theme-toggle-label">
        Appearance
      </span>
      <div
        className="theme-toggle__group"
        role="radiogroup"
        aria-labelledby="theme-toggle-label"
        onKeyDown={onKeyDown}
      >
        {OPTIONS.map((option) => {
          const selected = option.id === mode;
          return (
            <button
              key={option.id}
              type="button"
              role="radio"
              aria-checked={selected}
              tabIndex={selected ? 0 : -1}
              className={`theme-toggle__opt${selected ? ' is-selected' : ''}`}
              onClick={() => setMode(option.id)}
            >
              {option.id === 'light' ? (
                <SunIcon size={14} />
              ) : option.id === 'dark' ? (
                <MoonIcon size={14} />
              ) : (
                <SystemIcon size={14} />
              )}
              <span>{option.label}</span>
            </button>
          );
        })}
      </div>
      <span className="sr-only" aria-live="polite">
        {mode === 'system' ? `Following the system theme, currently ${resolved}.` : `${resolved} theme.`}
      </span>
    </div>
  );
}
