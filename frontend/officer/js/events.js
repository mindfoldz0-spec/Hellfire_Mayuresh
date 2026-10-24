/** Tiny application event bus built on browser CustomEvents. */

const PREFIX = "cers:";

export function emit(type, detail = {}) {
  window.dispatchEvent(new CustomEvent(`${PREFIX}${type}`, { detail }));
}

export function on(type, handler) {
  const listener = (event) => handler(event.detail);
  window.addEventListener(`${PREFIX}${type}`, listener);
  return () => window.removeEventListener(`${PREFIX}${type}`, listener);
}
