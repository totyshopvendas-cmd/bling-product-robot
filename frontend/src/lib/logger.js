/* Tiny logger — silent in production, verbose in dev. */
const isDev = process.env.NODE_ENV !== "production";

export const logger = {
  error: (...args) => { if (isDev) console.error(...args); },
  warn: (...args) => { if (isDev) console.warn(...args); },
  info: (...args) => { if (isDev) console.info(...args); },
};
