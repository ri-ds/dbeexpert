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
 * index.html for /admin.
 *
 * The admin view is temporary. When CCHMC sign in replaces the shared password
 * this check is the one place that decides the page.
 */
const path = window.location.pathname.replace(/\/+$/, '');
const isAdmin = path === '/admin';

createRoot(container).render(
  <StrictMode>{isAdmin ? <AdminFeedback /> : <App />}</StrictMode>,
);
