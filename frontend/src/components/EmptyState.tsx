import { GraphIcon, PeopleIcon, SparkIcon } from './Icons';

/** Starter questions offered before the first message. */
export const EXAMPLE_QUESTIONS: string[] = [
  'Who has expertise in longitudinal modeling of cystic fibrosis outcomes?',
  'Which faculty work on spatial methods and environmental exposure?',
  'Find faculty with experience in Bayesian adaptive clinical trial design.',
  'Who collaborates most often on pediatric cancer outcomes research?',
  'Which faculty publish on machine learning applied to electronic health records?',
  'List every faculty member and their number of publications.',
];

export interface EmptyStateProps {
  facultyCount: number;
  onPick: (question: string) => void;
}

export default function EmptyState({ facultyCount, onPick }: EmptyStateProps) {
  return (
    <section className="empty" aria-label="Getting started">
      <span className="empty__badge" aria-hidden="true">
        <SparkIcon size={20} />
      </span>
      <h2 className="empty__title">Explore DBE faculty expertise</h2>
      <p className="empty__lead">
        Ask a question in plain language. The Explorer searches a knowledge graph built
        from faculty CVs, publications, and abstracts, then explains why each person is
        relevant and how the answer was reached.
      </p>

      <ul className="empty__facts">
        <li>
          <span aria-hidden="true">
            <PeopleIcon size={14} />
          </span>
          <span>
            {facultyCount > 0 ? facultyCount : 20} faculty in the Division of
            Biostatistics and Epidemiology
          </span>
        </li>
        <li>
          <span aria-hidden="true">
            <GraphIcon size={14} />
          </span>
          <span>Grounded in a Neo4j graph, with the generated query shown on request</span>
        </li>
      </ul>

      <div className="empty__examples">
        <p className="empty__examples-caption" id="empty-examples-caption">
          Try one of these
        </p>
        <div className="chips" aria-labelledby="empty-examples-caption">
          {EXAMPLE_QUESTIONS.map((question) => (
            <button
              key={question}
              type="button"
              className="chip"
              onClick={() => onPick(question)}
            >
              {question}
            </button>
          ))}
        </div>
      </div>
    </section>
  );
}
