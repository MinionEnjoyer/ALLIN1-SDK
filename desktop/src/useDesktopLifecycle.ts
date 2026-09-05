import { useEffect, useRef, useState } from "react";
import type { DesktopClient } from "./types";

/** Native close and browser reload must honor the same workspace guards. */
export function useDesktopLifecycle(client: DesktopClient, guarded: boolean) {
  const current = useRef(guarded);
  current.current = guarded;
  const [notice, setNotice] = useState("");
  useEffect(() => {
    let active = true, closing = false;
    let unlisten: (() => void) | undefined;
    const close = async () => {
      if (!active || closing) return;
      if (current.current) {
        setNotice("Finish the current operation and save or reset your draft before closing the SDK.");
        return;
      }
      closing = true;
      try { await client.closeWindow(); }
      catch (error) { if (active) setNotice(String(error)); }
      finally { closing = false; }
    };
    void client.onCloseRequested(() => void close()).then(remove => {
      if (active) unlisten = remove; else remove();
    }).catch(error => { if (active) setNotice(`Could not register the window close guard: ${String(error)}`); });
    const unload = (event: BeforeUnloadEvent) => {
      if (current.current) { event.preventDefault(); event.returnValue = ""; }
    };
    window.addEventListener("beforeunload", unload);
    return () => { active = false; unlisten?.(); window.removeEventListener("beforeunload", unload); };
  }, [client]);
  return { notice, dismiss: () => setNotice("") };
}
