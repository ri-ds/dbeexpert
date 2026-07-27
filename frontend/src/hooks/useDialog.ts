import { useCallback, useEffect, useRef } from 'react';
import type { MutableRefObject } from 'react';

/**
 * The one modal keyboard contract in the app, shared by every dialog.
 *
 * Escape closes, focus moves in on open and is trapped while open, and the page
 * behind the dialog cannot scroll. Returning focus to the trigger is the
 * caller's job, since only the caller knows which control opened it.
 *
 * `summary` is in the selector because a disclosure inside a dialog is
 * focusable natively, and leaving it out let Tab escape the trap.
 */
const FOCUSABLE =
  'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), summary, [tabindex]:not([tabindex="-1"])';

function focusableIn(root: HTMLElement): HTMLElement[] {
  return Array.from(root.querySelectorAll<HTMLElement>(FOCUSABLE)).filter(
    (node) => node.offsetParent !== null || node === root,
  );
}

/**
 * Read only view of any element ref, so a caller can pass a ref holding a
 * textarea or an input without the invariance of MutableRefObject rejecting it.
 */
type ReadableRef = { readonly current: HTMLElement | null };

export interface UseDialogOptions {
  onClose: () => void;
  /**
   * Element that takes focus on open. Defaults to the panel, so the accessible
   * name is announced before the content is read. Point it at a field when the
   * dialog exists to be typed into.
   */
  initialFocusRef?: ReadableRef;
  /** Set false while a submission is in flight so Escape cannot abandon it. */
  dismissible?: boolean;
}

/**
 * Wire up one dialog. Attach the returned ref to the panel that carries
 * `role="dialog"` and `tabIndex={-1}`.
 */
export function useDialog({
  onClose,
  initialFocusRef,
  dismissible = true,
}: UseDialogOptions): MutableRefObject<HTMLDivElement | null> {
  const panelRef = useRef<HTMLDivElement | null>(null);

  // Move focus into the dialog exactly once, on open. Deliberately not keyed on
  // the ref, since focus must never be yanked again while the dialog is up.
  const focusTargetRef = useRef(initialFocusRef);
  useEffect(() => {
    const target = focusTargetRef.current?.current ?? panelRef.current;
    target?.focus();
  }, []);

  // Lock background scrolling for as long as the dialog is mounted.
  useEffect(() => {
    const previous = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      document.body.style.overflow = previous;
    };
  }, []);

  const onKeyDown = useCallback(
    (event: KeyboardEvent): void => {
      if (event.key === 'Escape') {
        event.preventDefault();
        if (dismissible) {
          onClose();
        }
        return;
      }
      if (event.key !== 'Tab') {
        return;
      }
      const panel = panelRef.current;
      if (panel === null) {
        return;
      }
      const nodes = focusableIn(panel);
      const first = nodes[0];
      const last = nodes[nodes.length - 1];
      if (first === undefined || last === undefined) {
        event.preventDefault();
        panel.focus();
        return;
      }

      const active = document.activeElement;
      // Focus can end up on the body after a click on static text, so pull it
      // back rather than letting Tab walk into the page behind the dialog.
      if (!(active instanceof HTMLElement) || !panel.contains(active)) {
        event.preventDefault();
        (event.shiftKey ? last : first).focus();
        return;
      }
      if (event.shiftKey && (active === first || active === panel)) {
        event.preventDefault();
        last.focus();
        return;
      }
      if (!event.shiftKey && active === last) {
        event.preventDefault();
        first.focus();
      }
    },
    [dismissible, onClose],
  );

  // Listened for on the document so the trap holds even if focus slips out.
  useEffect(() => {
    document.addEventListener('keydown', onKeyDown);
    return () => {
      document.removeEventListener('keydown', onKeyDown);
    };
  }, [onKeyDown]);

  return panelRef;
}
