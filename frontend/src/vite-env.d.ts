/// <reference types="vite/client" />

/**
 * Vite's ambient types, which is what declares `import.meta.env`.
 *
 * Needed because api.ts and main.tsx read `import.meta.env.BASE_URL` to learn
 * where the app is mounted. The types ship with Vite itself, so this reference
 * adds no dependency.
 */
