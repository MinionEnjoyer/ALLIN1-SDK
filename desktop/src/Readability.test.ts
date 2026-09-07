import { readFileSync, readdirSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const source = resolve(process.cwd(), "src");
const read = (file: string) => readFileSync(resolve(source, file), "utf8");
const theme = read("./readability.css");

describe("Launcher-aligned SDK readability", () => {
  it("loads the shared readability layer after the base styles", () => {
    const main = read("./main.tsx");
    expect(main.indexOf('"./readability.css"')).toBeGreaterThan(main.indexOf('"./styles.css"'));
  });

  it("keeps small UI copy on a 12px minimum, without scaling canvas or code metrics", () => {
    expect(read("./styles.css")).toContain("--font-caption: .75rem");
    expect(read("./styles.css")).toContain("--font-label: .8125rem");
    expect(read("./styles.css")).toContain("--font-body: .875rem");
    for (const file of readdirSync(source).filter(name => name.endsWith(".css") && !["GraphWorkbench.css", "code-editor.css"].includes(name))) {
      for (const declaration of read(`./${file}`).match(/\bfont(?:-size)?\s*:[^;}]+/g) ?? []) {
        for (const match of declaration.matchAll(/(?<![\w.-])(0?\.\d+)rem\b/g)) {
          expect(Number(match[1]), `${file}: ${declaration}`).toBeGreaterThanOrEqual(.75);
        }
      }
    }
    expect(theme).not.toMatch(/\bzoom\s*:|\btransform\s*:\s*scale/);
  });

  it("provides readable normal and secondary text in both themes", () => {
    const luminance = (hex: string) => {
      const rgb = hex.match(/[a-f\d]{2}/gi)!.map(part => {
        const s = parseInt(part, 16) / 255;
        return s <= .04045 ? s / 12.92 : ((s + .055) / 1.055) ** 2.4;
      });
      return rgb[0] * .2126 + rgb[1] * .7152 + rgb[2] * .0722;
    };
    for (const block of [theme.split(':root[data-theme="dark"]')[0], theme.split(':root[data-theme="dark"]')[1].split("}")[0]]) {
      const token = (name: string) => block.match(new RegExp(`--${name}: (#[a-f\\d]+)`))![1];
      for (const text of ["text", "muted", "subtle"]) {
        for (const surface of ["canvas", "surface-raised"]) {
          const values = [luminance(token(text)), luminance(token(surface))].sort((a, b) => b - a);
          expect((values[0] + .05) / (values[1] + .05), `${text} on ${surface}`).toBeGreaterThanOrEqual(4.5);
        }
      }
    }
  });
});
