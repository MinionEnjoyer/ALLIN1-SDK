import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

afterEach(() => {
  cleanup();
  localStorage.clear();
});

Object.defineProperty(window, "matchMedia", {
  writable: true,
  value: (query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addEventListener: () => undefined,
    removeEventListener: () => undefined,
    addListener: () => undefined,
    removeListener: () => undefined,
    dispatchEvent: () => false,
  }),
});

// jsdom has no layout engine. Keep CodeMirror's document/search/undo logic real,
// substituting only unavailable geometry APIs; these tests do not certify layout.
if (!Range.prototype.getClientRects) {
  Range.prototype.getClientRects = () => Object.assign([] as DOMRect[], { item: () => null });
  Range.prototype.getBoundingClientRect = () => new DOMRect(0, 0, 0, 0);
}
