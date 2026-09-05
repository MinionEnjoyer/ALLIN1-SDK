import { expect, it } from "vitest";
import { tauriWatchExclusion } from "./watchPolicy";

it.each([
  "C:\\SDK\\desktop\\src-tauri", "C:\\SDK\\desktop\\src-tauri\\target\\release\\locked.exe",
  "C:/SDK/desktop/src-tauri/target/release/index.html", "src-tauri", "src-tauri/Cargo.toml",
])("excludes the Rust tree before traversing it: %s", path => {
  expect(tauriWatchExclusion.test(path)).toBe(true);
});
it.each(["C:/SDK/desktop/src/WeaponCamera.tsx", "src-tauri-notes.md", "src/scopeCalibration.ts"])(
  "keeps frontend and similarly named files watched: %s", path => expect(tauriWatchExclusion.test(path)).toBe(false));
