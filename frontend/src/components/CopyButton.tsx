import { useCallback, useEffect, useRef, useState } from 'react';
import { CheckIcon, CopyIcon } from './Icons';

/** Copy text using the async clipboard API, with a textarea fallback. */
async function writeClipboard(text: string): Promise<boolean> {
  if (navigator.clipboard && typeof navigator.clipboard.writeText === 'function') {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch {
      // Permission denied or insecure context, try the legacy path.
    }
  }
  try {
    const area = document.createElement('textarea');
    area.value = text;
    area.setAttribute('readonly', 'true');
    area.style.position = 'fixed';
    area.style.top = '-1000px';
    area.style.opacity = '0';
    document.body.appendChild(area);
    area.select();
    const ok = document.execCommand('copy');
    document.body.removeChild(area);
    return ok;
  } catch {
    return false;
  }
}

export interface CopyButtonProps {
  text: string;
  /** Accessible label, for example "Copy Cypher query". */
  label: string;
  /** Show the word "Copy" next to the icon. */
  withText?: boolean;
}

export default function CopyButton({ text, label, withText = true }: CopyButtonProps) {
  const [state, setState] = useState<'idle' | 'copied' | 'failed'>('idle');
  const timer = useRef<number | null>(null);

  useEffect(
    () => () => {
      if (timer.current !== null) {
        window.clearTimeout(timer.current);
      }
    },
    [],
  );

  const onClick = useCallback(() => {
    void writeClipboard(text).then((ok) => {
      setState(ok ? 'copied' : 'failed');
      if (timer.current !== null) {
        window.clearTimeout(timer.current);
      }
      timer.current = window.setTimeout(() => setState('idle'), 1800);
    });
  }, [text]);

  const caption = state === 'copied' ? 'Copied' : state === 'failed' ? 'Failed' : 'Copy';

  return (
    <button
      type="button"
      className="copy-btn"
      onClick={onClick}
      aria-label={state === 'copied' ? `${label}, copied` : label}
    >
      {state === 'copied' ? <CheckIcon size={13} /> : <CopyIcon size={13} />}
      {withText ? <span>{caption}</span> : null}
      <span className="sr-only" role="status" aria-live="polite">
        {state === 'copied' ? 'Copied to clipboard' : ''}
      </span>
    </button>
  );
}
