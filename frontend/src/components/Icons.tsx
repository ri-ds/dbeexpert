/**
 * Hand written inline SVG icons. No icon package, no remote assets.
 *
 * Every icon draws on a 24 by 24 grid, inherits `currentColor`, and is marked
 * `aria-hidden` because the surrounding control always carries the label.
 */

import type { ReactNode } from 'react';

export interface IconProps {
  size?: number;
  className?: string;
  strokeWidth?: number;
}

interface BaseProps extends IconProps {
  children: ReactNode;
  /** Use `none` for pure fill icons. */
  fill?: string;
}

function Svg({
  size = 16,
  className,
  strokeWidth = 1.75,
  fill = 'none',
  children,
}: BaseProps) {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill={fill}
      stroke="currentColor"
      strokeWidth={strokeWidth}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
      {...(className ? { className } : {})}
    >
      {children}
    </svg>
  );
}

/* ------------------------------------------------------------------ */
/* Interface icons                                                     */
/* ------------------------------------------------------------------ */

export function MenuIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <line x1="3" y1="6" x2="21" y2="6" />
      <line x1="3" y1="12" x2="21" y2="12" />
      <line x1="3" y1="18" x2="21" y2="18" />
    </Svg>
  );
}

export function CloseIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <line x1="5" y1="5" x2="19" y2="19" />
      <line x1="19" y1="5" x2="5" y2="19" />
    </Svg>
  );
}

export function SunIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <circle cx="12" cy="12" r="4" />
      <line x1="12" y1="2" x2="12" y2="4.5" />
      <line x1="12" y1="19.5" x2="12" y2="22" />
      <line x1="2" y1="12" x2="4.5" y2="12" />
      <line x1="19.5" y1="12" x2="22" y2="12" />
      <line x1="4.9" y1="4.9" x2="6.7" y2="6.7" />
      <line x1="17.3" y1="17.3" x2="19.1" y2="19.1" />
      <line x1="19.1" y1="4.9" x2="17.3" y2="6.7" />
      <line x1="6.7" y1="17.3" x2="4.9" y2="19.1" />
    </Svg>
  );
}

export function MoonIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M20 14.5A8.5 8.5 0 0 1 9.5 4a7.5 7.5 0 1 0 10.5 10.5z" />
    </Svg>
  );
}

export function SystemIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <rect x="2.5" y="4" width="19" height="12.5" rx="2" />
      <line x1="8.5" y1="20.5" x2="15.5" y2="20.5" />
      <line x1="12" y1="16.5" x2="12" y2="20.5" />
    </Svg>
  );
}

export function CopyIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <rect x="9" y="9" width="11.5" height="11.5" rx="2" />
      <path d="M6.5 15H5a1.5 1.5 0 0 1-1.5-1.5V5A1.5 1.5 0 0 1 5 3.5h8.5A1.5 1.5 0 0 1 15 5v1.5" />
    </Svg>
  );
}

export function CheckIcon(props: IconProps) {
  return (
    <Svg {...props} strokeWidth={props.strokeWidth ?? 2.25}>
      <polyline points="4.5,12.5 9.5,17.5 19.5,6.5" />
    </Svg>
  );
}

export function SendIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M4 11.5 20 4l-7.5 16-2-6.5z" />
      <line x1="10.5" y1="13.5" x2="20" y2="4" />
    </Svg>
  );
}

export function StopIcon(props: IconProps) {
  return (
    <Svg {...props} fill="currentColor" strokeWidth={0}>
      <rect x="6.5" y="6.5" width="11" height="11" rx="1.5" />
    </Svg>
  );
}

export function PlusIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <line x1="12" y1="5" x2="12" y2="19" />
      <line x1="5" y1="12" x2="19" y2="12" />
    </Svg>
  );
}

export function ChevronIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <polyline points="8,4.5 15.5,12 8,19.5" />
    </Svg>
  );
}

export function AlertIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M12 3.8 21 19.5H3z" />
      <line x1="12" y1="9.5" x2="12" y2="14" />
      <circle cx="12" cy="16.75" r="0.9" fill="currentColor" strokeWidth={0} />
    </Svg>
  );
}

export function InfoIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <circle cx="12" cy="12" r="9" />
      <line x1="12" y1="11" x2="12" y2="16.5" />
      <circle cx="12" cy="7.75" r="0.9" fill="currentColor" strokeWidth={0} />
    </Svg>
  );
}

/** Feedback: a speech bubble, used on the per answer feedback trigger. */
export function FeedbackIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M20.5 13.5a2.5 2.5 0 0 1-2.5 2.5H9l-4.5 4V6a2.5 2.5 0 0 1 2.5-2.5h11A2.5 2.5 0 0 1 20.5 6z" />
      <line x1="8.5" y1="8.5" x2="16" y2="8.5" />
      <line x1="8.5" y1="11.5" x2="13" y2="11.5" />
    </Svg>
  );
}

export function TrashIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <line x1="4" y1="6.5" x2="20" y2="6.5" />
      <path d="M6.5 6.5 7.5 20a1.5 1.5 0 0 0 1.5 1.4h6a1.5 1.5 0 0 0 1.5-1.4l1-13.5" />
      <path d="M9.5 6.5V4.6a1.2 1.2 0 0 1 1.2-1.1h2.6a1.2 1.2 0 0 1 1.2 1.1v1.9" />
    </Svg>
  );
}

export function GraphIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <circle cx="6" cy="17.5" r="2.6" />
      <circle cx="18" cy="17.5" r="2.6" />
      <circle cx="12" cy="5.5" r="2.6" />
      <line x1="7.6" y1="15.4" x2="10.4" y2="7.6" />
      <line x1="13.6" y1="7.6" x2="16.4" y2="15.4" />
      <line x1="8.6" y1="17.5" x2="15.4" y2="17.5" />
    </Svg>
  );
}

export function PeopleIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <circle cx="9.5" cy="8" r="3.2" />
      <path d="M3.5 20c0-3.1 2.7-5.4 6-5.4s6 2.3 6 5.4" />
      <path d="M16.5 5.4a3.2 3.2 0 0 1 0 6" />
      <path d="M18 14.9c1.7.8 2.8 2.4 2.8 4.3" />
    </Svg>
  );
}

export function SparkIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M12 3.5 13.8 9l5.5 1.8-5.5 1.8L12 18l-1.8-5.4L4.7 10.8 10.2 9z" />
      <line x1="18.5" y1="4" x2="18.5" y2="7" />
      <line x1="17" y1="5.5" x2="20" y2="5.5" />
    </Svg>
  );
}

/* ------------------------------------------------------------------ */
/* Pipeline stage icons                                                */
/* ------------------------------------------------------------------ */

/** classify: sorting a question into a bucket. */
export function ClassifyIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M3.5 5.5h17l-6.5 7.6v5.9l-4 2v-7.9z" />
    </Svg>
  );
}

/** route: choosing a path. */
export function RouteIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <circle cx="6" cy="6" r="2.4" />
      <circle cx="18" cy="18" r="2.4" />
      <path d="M6 8.4v4.1a3.5 3.5 0 0 0 3.5 3.5H15.6" />
      <polyline points="13.6,14 15.6,16 13.6,18" />
    </Svg>
  );
}

/** retrieve: pulling documents out of the graph. */
export function RetrieveIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <circle cx="10.5" cy="10.5" r="6" />
      <line x1="15" y1="15" x2="20.5" y2="20.5" />
    </Svg>
  );
}

/** judge: weighing relevance. */
export function JudgeIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <line x1="12" y1="4" x2="12" y2="20" />
      <line x1="6.5" y1="20" x2="17.5" y2="20" />
      <line x1="4.5" y1="7.5" x2="19.5" y2="7.5" />
      <path d="M4.5 7.5 2.5 13h4z" />
      <path d="M19.5 7.5 17.5 13h4z" />
    </Svg>
  );
}

/** rank: ordered bars. */
export function RankIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <line x1="4" y1="6.5" x2="19" y2="6.5" />
      <line x1="4" y1="12" x2="14" y2="12" />
      <line x1="4" y1="17.5" x2="9" y2="17.5" />
    </Svg>
  );
}

/** extract: lifting structured facts out of prose. */
export function ExtractIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M13.5 3.5H6.5A1.5 1.5 0 0 0 5 5v14a1.5 1.5 0 0 0 1.5 1.5h11A1.5 1.5 0 0 0 19 19V9z" />
      <polyline points="13.5,3.5 13.5,9 19,9" />
      <line x1="8.5" y1="13" x2="15" y2="13" />
      <line x1="8.5" y1="16.5" x2="13" y2="16.5" />
    </Svg>
  );
}

/** cypher_generate: writing a query. */
export function CypherGenerateIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <polyline points="8.5,8 4.5,12 8.5,16" />
      <polyline points="15.5,8 19.5,12 15.5,16" />
      <line x1="13.2" y1="5.5" x2="10.8" y2="18.5" />
    </Svg>
  );
}

/** cypher_execute: running a query against the database. */
export function CypherExecuteIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <ellipse cx="12" cy="5.8" rx="7" ry="2.8" />
      <path d="M5 5.8v12.4c0 1.5 3.1 2.8 7 2.8s7-1.3 7-2.8V5.8" />
      <path d="M5 12c0 1.5 3.1 2.8 7 2.8s7-1.3 7-2.8" />
    </Svg>
  );
}

/** answer: composing the reply. */
export function AnswerIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M20.5 12.8c0 3.9-3.8 7-8.5 7-1 0-2-.1-2.9-.4l-4.6 1.6 1.4-4.1a6.6 6.6 0 0 1-2-4.7c0-3.9 3.8-7 8.1-7s8.5 3.1 8.5 7.6z" />
    </Svg>
  );
}

/** Fallback used when the backend sends an unrecognized stage id. */
export function DotIcon(props: IconProps) {
  return (
    <Svg {...props} fill="currentColor" strokeWidth={0}>
      <circle cx="12" cy="12" r="4" />
    </Svg>
  );
}
