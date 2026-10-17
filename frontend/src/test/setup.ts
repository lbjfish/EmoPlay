import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

afterEach(() => cleanup());

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

window.requestAnimationFrame = (callback) => window.setTimeout(() => callback(Date.now()), 0);
window.cancelAnimationFrame = (handle) => window.clearTimeout(handle);

class ObserverStub {
  constructor(...arguments_: unknown[]) {
    void arguments_;
  }

  observe(): void {}

  unobserve(): void {}

  disconnect(): void {}

  takeRecords(): unknown[] {
    return [];
  }
}

Object.defineProperty(window, "IntersectionObserver", { writable: true, value: ObserverStub });
Object.defineProperty(window, "ResizeObserver", { writable: true, value: ObserverStub });
