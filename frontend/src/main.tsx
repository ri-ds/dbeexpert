import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import App from './App';
import AdminFeedback from './components/AdminFeedback';
import './styles/global.css';
import './styles/app.css';

const container = document.getElementById('root');

if (!container) {
  throw new Error('Root element not found. Check index.html.');
}

/**
 * There are exactly two pages, so there is no router. A pathname check is
 * enough, and both the dev server and the nginx history fallback already serve
 * index.html for the admin path.
 *
 * The comparison is made relative to where the app is mounted, so it works at
 * the root (`/admin`) and under a sub path (`/expert/admin`) without knowing
 * which. Comparing against a bare `/admin` silently rendered the chat instead of
 * the admin page once the app moved under a prefix.
 *
 * The admin view is temporary. When CCHMC sign in replaces the shared password
 * this check is the one place that decides the page.
 */
const base = (import.meta.env.BASE_URL || '/').replace(/\/+$/, '');
const path = window.location.pathname.replace(/\/+$/, '');
const isAdmin = path === `${base}/admin`;

createRoot(container).render(
  <StrictMode>{isAdmin ? <AdminFeedback /> : <App />}</StrictMode>,
);
