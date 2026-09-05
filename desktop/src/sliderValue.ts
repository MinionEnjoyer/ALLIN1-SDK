// Empty/partial input is not zero. Keep text intact, including source precision.
export function sliderNumber(text: string): number {
  if (!/^[+-]?(?:\d+\.?\d*|\.\d+)(?:e[+-]?\d+)?$/i.test(text.trim())) return NaN;
  const value = Number(text);
  return Number.isFinite(value) ? value : NaN;
}
