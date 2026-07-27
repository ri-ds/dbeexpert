import { useId } from 'react';
import { useDialog } from '../hooks/useDialog';
import type { GraphStats } from '../types';
import { CloseIcon } from './Icons';

/** How many of the ranked label and relationship lists to show. */
const TOP_N = 10;

function formatCount(value: number | null | undefined): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) {
    return 'unknown';
  }
  return value.toLocaleString('en-US');
}

export interface InfoDialogProps {
  faculty: string[];
  graph: GraphStats | null;
  documentCategories: string[];
  onClose: () => void;
}

/**
 * Reference material that used to crowd the sidebar: what is in the knowledge
 * graph and who is covered.
 *
 * Search mode descriptions deliberately live with the mode control under the
 * composer rather than here, so there is one place to read them.
 *
 * A modal dialog with the full keyboard contract, which lives in useDialog and
 * is shared with the feedback form. Escape and the backdrop close it, focus
 * moves in on open and is trapped while open, and the page behind it cannot
 * scroll. Returning focus to the trigger is the caller's job, since only the
 * caller knows which control opened it.
 */
export default function InfoDialog({
  faculty,
  graph,
  documentCategories,
  onClose,
}: InfoDialogProps) {
  const panelRef = useDialog({ onClose });
  const titleId = useId();

  const labels = graph?.labels ?? [];
  const relTypes = graph?.relTypes ?? [];

  return (
    <>
      <div className="backdrop" onClick={onClose} aria-hidden="true" />
      <div
        className="modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        ref={panelRef}
        tabIndex={-1}
      >
        <header className="modal__head">
          <h2 className="modal__title" id={titleId}>
            About this Explorer
          </h2>
          <button
            type="button"
            className="icon-btn"
            onClick={onClose}
            aria-label="Close the information panel"
          >
            <CloseIcon size={16} />
          </button>
        </header>

        <div className="modal__body">
          <p className="modal__lead">
            Questions are answered from a Neo4j knowledge graph built out of faculty CVs,
            publications, and abstracts for the Division of Biostatistics and Epidemiology.
          </p>

          <section className="info-block" aria-labelledby={`${titleId}-graph`}>
            <h3 className="info-title" id={`${titleId}-graph`}>
              Knowledge graph
            </h3>

            <dl className="info-stats">
              <div className="info-stat">
                <dt>Nodes</dt>
                <dd>{formatCount(graph?.nodes)}</dd>
              </div>
              <div className="info-stat">
                <dt>Relationships</dt>
                <dd>{formatCount(graph?.relationships)}</dd>
              </div>
            </dl>

            {labels.length > 0 ? (
              <div className="info-sub">
                <h4 className="info-sub__title">
                  Most common node labels
                  <span className="info-sub__note">
                    top {Math.min(TOP_N, labels.length)} of {labels.length}
                  </span>
                </h4>
                <ul className="info-tags">
                  {labels.slice(0, TOP_N).map((item) => (
                    <li key={item.label} className="info-tag">
                      <span className="info-tag__name">{item.label}</span>
                      <span className="info-tag__count">{formatCount(item.count)}</span>
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}

            {relTypes.length > 0 ? (
              <div className="info-sub">
                <h4 className="info-sub__title">
                  Most common relationship types
                  <span className="info-sub__note">
                    top {Math.min(TOP_N, relTypes.length)} of {relTypes.length}
                  </span>
                </h4>
                <ul className="info-tags">
                  {relTypes.slice(0, TOP_N).map((item) => (
                    <li key={item.type} className="info-tag">
                      <span className="info-tag__name">{item.type}</span>
                      <span className="info-tag__count">{formatCount(item.count)}</span>
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}

            {documentCategories.length > 0 ? (
              <div className="info-sub">
                <h4 className="info-sub__title">
                  Document categories
                  <span className="info-sub__note">{documentCategories.length}</span>
                </h4>
                <ul className="info-tags">
                  {documentCategories.map((category) => (
                    <li key={category} className="info-tag">
                      <span className="info-tag__name">{category}</span>
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}

            {graph === null ? (
              <p className="info-hint">Graph details load once the backend responds.</p>
            ) : null}
          </section>

          {/* No search mode section. The mode control sits directly under the
              send button and carries each mode's description with it, so
              repeating them here was redundant. */}

          <section className="info-block" aria-labelledby={`${titleId}-faculty`}>
            <h3 className="info-title" id={`${titleId}-faculty`}>
              Faculty
              <span className="info-sub__note">{faculty.length}</span>
            </h3>
            {faculty.length === 0 ? (
              <p className="info-hint">The roster loads once the backend responds.</p>
            ) : (
              <ul className="info-faculty">
                {faculty.map((name) => (
                  <li key={name}>{name}</li>
                ))}
              </ul>
            )}
          </section>
        </div>
      </div>
    </>
  );
}
