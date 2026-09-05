import { describe, expect, it } from "vitest";
import { formatBytes, tokenizeCommandLine } from "./tokenize";

describe("tokenizeCommandLine", () => {
  it("produces literal argv without shell expansion", () => {
    expect(tokenizeCommandLine('inspect-rpf "C:\\Mods\\demo.rpf" --gta-path "D:\\GTA V"')).toEqual([
      "inspect-rpf",
      "C:\\Mods\\demo.rpf",
      "--gta-path",
      "D:\\GTA V",
    ]);
    expect(tokenizeCommandLine("validate 'C:/My Mods/addon.json'")).toEqual([
      "validate",
      "C:/My Mods/addon.json",
    ]);
    expect(tokenizeCommandLine("inspect-source $(danger)")).toEqual([
      "inspect-source",
      "$(danger)",
    ]);
  });

  it("rejects ambiguous incomplete quoting", () => {
    expect(() => tokenizeCommandLine('validate "broken')).toThrow("Unclosed quote");
  });
});

describe("formatBytes", () => {
  it("uses bounded binary units", () => {
    expect(formatBytes(512)).toBe("512 B");
    expect(formatBytes(1536)).toBe("1.50 KiB");
  });
});
