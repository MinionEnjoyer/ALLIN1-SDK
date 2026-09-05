/** Split console text into literal argv values without shell expansion. */
export function tokenizeCommandLine(value: string): string[] {
  const tokens: string[] = [];
  let token = "";
  let quote: "'" | '"' | null = null;
  let started = false;

  const input = value.trim();
  for (let index = 0; index < input.length; index += 1) {
    const character = input[index];
    if (quote) {
      if (character === "\\" && quote === '"' && input[index + 1] === '"') {
        token += '"';
        index += 1;
        started = true;
        continue;
      }
      if (character === quote) {
        quote = null;
      } else {
        token += character;
      }
      started = true;
      continue;
    }
    if (character === '"' || character === "'") {
      quote = character;
      started = true;
      continue;
    }
    if (/\s/.test(character)) {
      if (started) {
        tokens.push(token);
        token = "";
        started = false;
      }
      continue;
    }
    token += character;
    started = true;
  }
  if (quote) throw new Error("Unclosed quote in command");
  if (started) tokens.push(token);
  return tokens;
}

export function formatBytes(value: unknown): string {
  const bytes = typeof value === "number" ? value : Number(value ?? 0);
  if (!Number.isFinite(bytes) || bytes < 0) return "—";
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KiB", "MiB", "GiB"];
  let amount = bytes;
  let unit = -1;
  do {
    amount /= 1024;
    unit += 1;
  } while (amount >= 1024 && unit < units.length - 1);
  return `${amount.toFixed(amount >= 10 ? 1 : 2)} ${units[unit]}`;
}
