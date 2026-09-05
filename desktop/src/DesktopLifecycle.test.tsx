import { act, renderHook, waitFor } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { createPreviewClient } from "./previewClient";
import { useDesktopLifecycle } from "./useDesktopLifecycle";

it("blocks native close and reload for drafts, then closes after they are saved", async () => {
  const client = createPreviewClient("empty");
  let requestClose = () => {};
  client.onCloseRequested = vi.fn(async handler => { requestClose = handler; return () => {}; });
  client.closeWindow = vi.fn(async () => {});
  const { result, rerender } = renderHook(({ guarded }) => useDesktopLifecycle(client, guarded), { initialProps: { guarded: true } });
  await waitFor(() => expect(client.onCloseRequested).toHaveBeenCalled());
  act(requestClose);
  expect(client.closeWindow).not.toHaveBeenCalled();
  expect(result.current.notice).toMatch(/save or reset/);
  const unload = new Event("beforeunload", { cancelable: true });
  window.dispatchEvent(unload);
  expect(unload.defaultPrevented).toBe(true);
  rerender({ guarded: false });
  act(requestClose);
  await waitFor(() => expect(client.closeWindow).toHaveBeenCalledTimes(1));
});

it("surfaces the native in-flight operation refusal without closing or retrying", async () => {
  const client = createPreviewClient("empty");
  let requestClose = () => {};
  client.onCloseRequested = async handler => { requestClose = handler; return () => {}; };
  client.closeWindow = vi.fn(async () => { throw new Error("An SDK operation is still running"); });
  const { result } = renderHook(() => useDesktopLifecycle(client, false));
  act(requestClose);
  await waitFor(() => expect(result.current.notice).toMatch(/still running/));
  expect(client.closeWindow).toHaveBeenCalledTimes(1);
});

it("unregisters a delayed listener after the component is unmounted", async () => {
  const client = createPreviewClient("empty");
  let complete: (unlisten: () => void) => void = () => {};
  const remove = vi.fn();
  client.onCloseRequested = () => new Promise(resolve => { complete = resolve; });
  const { unmount } = renderHook(() => useDesktopLifecycle(client, false));
  unmount();
  await act(async () => complete(remove));
  expect(remove).toHaveBeenCalledTimes(1);
});
