import { useCallback, useEffect, useState } from 'react';

export type ThemeMode = 'light' | 'dark' | 'system';
export type ResolvedTheme = 'light' | 'dark';

/** Storage key, kept in sync with the inline bootstrap script in index.html. */
const STORAGE_KEY = 'dbe.theme';

function isThemeMode(value: unknown): value is ThemeMode {
  return value === 'light' || value === 'dark' || value === 'system';
}

function readStoredMode(): ThemeMode {
  try {
    const stored = window.localStorage.getItem(STORAGE_KEY);
    if (isThemeMode(stored)) {
      return stored;
    }
  } catch {
    // Storage can be unavailable in private browsing modes.
  }
  return 'system';
}

function systemPrefersDark(): boolean {
  if (typeof window.matchMedia !== 'function') {
    return false;
  }
  return window.matchMedia('(prefers-color-scheme: dark)').matches;
}

function resolve(mode: ThemeMode, prefersDark: boolean): ResolvedTheme {
  if (mode === 'system') {
    return prefersDark ? 'dark' : 'light';
  }
  return mode;
}

export interface UseThemeResult {
  /** What the user chose, which may be `system`. */
  mode: ThemeMode;
  /** What is actually applied to the document right now. */
  resolved: ResolvedTheme;
  setMode: (next: ThemeMode) => void;
  /** Step through light, dark, then system. */
  cycleMode: () => void;
}

/**
 * Theme controller. Defaults to the operating system preference, can be
 * overridden by the toggle, and persists the choice to localStorage. The
 * resolved value is written to `data-theme` on the html element.
 */
export function useTheme(): UseThemeResult {
  const [mode, setModeState] = useState<ThemeMode>(() => readStoredMode());
  const [prefersDark, setPrefersDark] = useState<boolean>(() => systemPrefersDark());

  // Track the system preference so `system` mode stays live.
  useEffect(() => {
    if (typeof window.matchMedia !== 'function') {
      return;
    }
    const query = window.matchMedia('(prefers-color-scheme: dark)');
    const onChange = (event: MediaQueryListEvent): void => {
      setPrefersDark(event.matches);
    };
    query.addEventListener('change', onChange);
    return () => {
      query.removeEventListener('change', onChange);
    };
  }, []);

  const resolved = resolve(mode, prefersDark);

  // Apply to the document and mirror into the native color scheme hint.
  useEffect(() => {
    const root = document.documentElement;
    root.setAttribute('data-theme', resolved);
    root.style.colorScheme = resolved;
  }, [resolved]);

  const setMode = useCallback((next: ThemeMode) => {
    setModeState(next);
    try {
      window.localStorage.setItem(STORAGE_KEY, next);
    } catch {
      // Persisting is best effort only.
    }
  }, []);

  const cycleMode = useCallback(() => {
    setModeState((current) => {
      const next: ThemeMode =
        current === 'light' ? 'dark' : current === 'dark' ? 'system' : 'light';
      try {
        window.localStorage.setItem(STORAGE_KEY, next);
      } catch {
        // Persisting is best effort only.
      }
      return next;
    });
  }, []);

  return { mode, resolved, setMode, cycleMode };
}
