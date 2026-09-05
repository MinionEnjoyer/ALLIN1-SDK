import { useId, useState } from "react";
import { sliderNumber } from "./sliderValue";
import "./SliderField.css";

interface Options {
  id?: string; label: string; hint?: string; unit?: string;
  min: number; max: number; step: number; fineStep?: number;
  hardMin?: number; hardMax?: number; disabled?: boolean;
  // Pure view transforms retain an invalid local draft without poisoning a camera.
  commitValidOnly?: boolean;
  endpoints?: [string, string]; describedBy?: string;
}
type Props = Options & (
  | { numeric: true; value: number; resetValue?: number; onChange: (value: number) => void }
  | { numeric?: false; value: string; resetValue?: string; onChange: (value: string) => void }
);

export default function SliderField(props: Props) {
  const generatedId = useId();
  const id = props.id ?? `slider-${generatedId}`;
  // Numeric consumers need a text draft too: typing "-" or "1." must work.
  // Invalid numeric drafts propagate NaN, allowing owners to block review/render.
  const [draft, setDraft] = useState({ source: props.value, text: String(props.value) });
  if (!Object.is(draft.source, props.value)) {
    setDraft({ source: props.value, text: String(props.value) });
  }
  const text = props.numeric ? draft.text : props.value;
  const number = sliderNumber(text);
  const invalid = !Number.isFinite(number)
    || (props.hardMin !== undefined && number < props.hardMin)
    || (props.hardMax !== undefined && number > props.hardMax);
  const outside = !invalid && (number < props.min || number > props.max);
  const thumb = Number.isFinite(number) ? Math.min(props.max, Math.max(props.min, number)) : props.min;
  const emit = (next: string) => {
    if (props.disabled) return;
    if (props.numeric) {
      const parsed = sliderNumber(next);
      if (props.commitValidOnly && (!Number.isFinite(parsed)
        || (props.hardMin !== undefined && parsed < props.hardMin)
        || (props.hardMax !== undefined && parsed > props.hardMax))) {
        setDraft({ source: props.value, text: next });
        return;
      }
      setDraft({ source: parsed, text: next });
      props.onChange(parsed);
    } else props.onChange(next);
  };
  const adjust = (value: number) => emit(String(Number(Math.min(props.max, Math.max(props.min, value)).toFixed(8))));
  const unit = props.unit ? ` ${props.unit}` : "";
  const note = invalid ? `Enter a finite value${props.hardMin !== undefined ? ` ≥ ${props.hardMin}` : ""}${props.hardMax !== undefined ? ` and ≤ ${props.hardMax}` : ""}${unit}.`
    : outside ? "Outside slider range; exact value preserved. Dragging uses the displayed range." : "";
  const helpId = `${id}-help`;
  const description = [props.describedBy, helpId].filter(Boolean).join(" ");
  return <div className="sdk-slider-field" data-invalid={invalid || undefined}>
    <label htmlFor={id}>{props.label}{props.unit && <small>{props.unit}</small>}</label>
    <div className="sdk-slider-controls">
      <div className="sdk-slider-track">
        <input type="range" min={props.min} max={props.max} step="any" value={thumb}
          aria-label={`${props.label} slider`} aria-valuetext={invalid ? "Invalid draft; choose a value" : `${text}${unit}${outside ? "; outside slider range" : ""}`}
          aria-describedby={description} disabled={props.disabled}
          onChange={event => adjust(props.min + Math.round((Number(event.target.value) - props.min) / props.step) * props.step)}
          onKeyDown={event => {
            const directions: Record<string, number> = { ArrowRight: 1, ArrowUp: 1, ArrowLeft: -1, ArrowDown: -1, PageUp: 10, PageDown: -10 };
            if (!(event.key in directions) && event.key !== "Home" && event.key !== "End") return;
            event.preventDefault();
            if (event.key === "Home") adjust(props.min);
            else if (event.key === "End") adjust(props.max);
            else adjust(thumb + directions[event.key] * (event.shiftKey ? (props.fineStep ?? props.step / 10) : props.step));
          }} />
        <div className="sdk-slider-endpoints" aria-hidden="true"><span>{props.endpoints?.[0] ?? props.min}</span><span>{props.endpoints?.[1] ?? props.max}</span></div>
      </div>
      <input id={id} className="sdk-slider-value" type="text" inputMode="decimal" value={text}
        aria-label={props.label} aria-invalid={invalid} aria-describedby={description}
        disabled={props.disabled} onChange={event => emit(event.target.value)} />
      {props.resetValue !== undefined && <button className="sdk-slider-reset" type="button" aria-label={`Reset ${props.label}`}
        title="Restore the source/default value" disabled={props.disabled || text === String(props.resetValue)}
        onClick={() => emit(String(props.resetValue))}>↺</button>}
    </div>
    <small id={helpId} className="sdk-slider-help">{props.hint && <span>{props.hint} </span>}<span>{note || "Shift + arrow for fine adjustment."}</span></small>
  </div>;
}
