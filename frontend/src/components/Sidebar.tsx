import type { Conversation } from '../hooks/useConversations';
import type { ResolvedTheme, ThemeMode } from '../hooks/useTheme';
import { CloseIcon, GraphIcon, PlusIcon, TrashIcon } from './Icons';
import ThemeToggle from './ThemeToggle';

export interface SidebarProps {
  /** Already sorted, most recently updated first. */
  conversations: Conversation[];
  activeId: string;
  onSelectConversation: (id: string) => void;
  onDeleteConversation: (id: string) => void;
  onNewChat: () => void;
  themeMode: ThemeMode;
  themeResolved: ResolvedTheme;
  setThemeMode: (next: ThemeMode) => void;
  /** Drawer mode is used below 900px wide. */
  isDrawer: boolean;
  onClose: () => void;
}

/** Short local date, for example Jul 24 or Jul 24, 2025 for another year. */
function formatDay(timestamp: number): string {
  try {
    const date = new Date(timestamp);
    const sameYear = date.getFullYear() === new Date().getFullYear();
    return date.toLocaleDateString('en-US', {
      month: 'short',
      day: 'numeric',
      ...(sameYear ? {} : { year: 'numeric' }),
    });
  } catch {
    return '';
  }
}

/**
 * Three things only: start a new chat, reopen a past one, and set the
 * appearance. Everything that used to live here is reference material and now
 * sits behind the Info button in the top bar.
 */
export default function Sidebar({
  conversations,
  activeId,
  onSelectConversation,
  onDeleteConversation,
  onNewChat,
  themeMode,
  themeResolved,
  setThemeMode,
  isDrawer,
  onClose,
}: SidebarProps) {
  // An untouched conversation is not history yet, so it stays out of the list
  // until it has something in it.
  const history = conversations.filter(
    (conversation) => conversation.messages.length > 0,
  );

  return (
    <aside className="sidebar" aria-label="Conversations">
      <div className="sidebar__top">
        <div className="brand">
          <span className="brand__mark" aria-hidden="true">
            <GraphIcon size={17} />
          </span>
          <span className="brand__name">Faculty Expertise Explorer</span>
        </div>
        {isDrawer ? (
          <button
            type="button"
            className="icon-btn sidebar__close"
            onClick={onClose}
            aria-label="Close the menu"
          >
            <CloseIcon size={16} />
          </button>
        ) : null}
      </div>

      <div className="sidebar__new">
        <button type="button" className="btn btn--ghost btn--block" onClick={onNewChat}>
          <PlusIcon size={14} />
          <span>New Chat</span>
        </button>
      </div>

      <nav className="history" aria-label="Chat history">
        <h2 className="side-title history__caption">History</h2>
        {history.length === 0 ? (
          <p className="history__empty">
            Conversations you have had appear here once you ask something.
          </p>
        ) : (
          <ul className="history__list">
            {history.map((conversation) => {
              const isActive = conversation.id === activeId;
              return (
                <li
                  key={conversation.id}
                  className={`history__row${isActive ? ' is-active' : ''}`}
                >
                  <button
                    type="button"
                    className="history__open"
                    title={conversation.title}
                    aria-current={isActive ? 'true' : undefined}
                    onClick={() => onSelectConversation(conversation.id)}
                  >
                    <span className="history__title">{conversation.title}</span>
                    <span className="history__when">
                      {formatDay(conversation.updatedAt)}
                    </span>
                  </button>
                  <button
                    type="button"
                    className="history__del"
                    onClick={() => onDeleteConversation(conversation.id)}
                    aria-label={`Delete the conversation ${conversation.title}`}
                  >
                    <TrashIcon size={13} />
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </nav>

      <div className="sidebar__foot">
        <ThemeToggle mode={themeMode} resolved={themeResolved} setMode={setThemeMode} />
      </div>
    </aside>
  );
}
