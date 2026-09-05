import { useEffect, useRef } from "react";
import { basicSetup } from "codemirror";
import { Compartment, EditorState } from "@codemirror/state";
import { EditorView } from "@codemirror/view";
import { HighlightStyle, StreamLanguage, syntaxHighlighting } from "@codemirror/language";
import { tags } from "@lezer/highlight";
import { xml } from "@codemirror/lang-xml";
import { lua } from "@codemirror/legacy-modes/mode/lua";
import { openSearchPanel } from "@codemirror/search";

export default function CodeEditor({ value, language, lineEnding, locked, onChange }: {
  value: string; language: "xml" | "lua"; lineEnding: "LF" | "CRLF"; locked: boolean; onChange: (text: string) => void;
}) {
  const host = useRef<HTMLDivElement>(null), editor = useRef<EditorView | null>(null);
  const editable = useRef(new Compartment()), change = useRef(onChange), current = useRef(value);
  change.current = onChange; current.current = value;
  useEffect(() => {
    if (!host.current) return;
    const view = new EditorView({ parent: host.current, doc: current.current, extensions: [
      basicSetup, language === "xml" ? xml() : StreamLanguage.define(lua),
      syntaxHighlighting(HighlightStyle.define([
        { tag: tags.comment, color: "var(--muted)" },
        { tag: [tags.keyword, tags.tagName], color: "var(--code-keyword)" },
        { tag: [tags.string, tags.attributeValue], color: "var(--code-string)" },
        { tag: [tags.number, tags.bool, tags.null, tags.attributeName], color: "var(--code-value)" },
        { tag: tags.invalid, textDecoration: "underline wavy var(--red-500)" },
      ])),
      EditorState.lineSeparator.of(lineEnding === "CRLF" ? "\r\n" : "\n"),
      editable.current.of(EditorState.readOnly.of(false)),
      EditorView.contentAttributes.of({ "aria-label": `${language.toUpperCase()} source editor`, "aria-multiline": "true", spellcheck: "false" }),
      EditorView.updateListener.of(update => {
        if (update.docChanged) change.current(update.state.sliceDoc());
      }),
      EditorView.theme({
        "&": { height: "100%", color: "var(--text)", backgroundColor: "var(--surface)" },
        ".cm-scroller": { overflow: "auto", fontFamily: 'Consolas, "Cascadia Code", monospace', fontSize: "14px", lineHeight: "1.65" },
        ".cm-content": { padding: "12px 0", caretColor: "var(--text)" },
        ".cm-gutters": { backgroundColor: "var(--surface-muted)", color: "var(--muted)", borderRight: "1px solid var(--border)" },
        ".cm-activeLine, .cm-activeLineGutter": { backgroundColor: "var(--accent-soft)" },
        ".cm-cursor": { borderLeftColor: "var(--text)" },
        ".cm-panels": { backgroundColor: "var(--surface-muted)", color: "var(--text)" },
        "&.cm-focused .cm-selectionBackground, .cm-selectionBackground": { backgroundColor: "#567e6650" },
      }),
    ] });
    editor.current = view;
    return () => { editor.current = null; view.destroy(); };
  }, [language, lineEnding]);
  useEffect(() => {
    const view = editor.current;
    if (view && value !== view.state.sliceDoc()) view.dispatch({ changes: { from: 0, to: view.state.doc.length, insert: value } });
  }, [value]);
  useEffect(() => { editor.current?.dispatch({ effects: editable.current.reconfigure(EditorState.readOnly.of(locked)) }); }, [locked, language, lineEnding]);
  return <div className="code-editor-shell"><div className="code-editor-toolbar">
    <span>Line numbers · syntax highlighting · undo/redo</span>
    <button type="button" className="quiet-button" onClick={() => { if (editor.current) openSearchPanel(editor.current); }}>Find / replace</button>
  </div><div ref={host} className="code-editor-surface" /></div>;
}
