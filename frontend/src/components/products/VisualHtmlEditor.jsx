import { useEffect, useRef, useState } from "react";
import { Code, Eye, ListBullets, ListNumbers, TextB, TextItalic } from "@phosphor-icons/react";

function run(command, value = null) {
    document.execCommand(command, false, value);
}

export default function VisualHtmlEditor({ value = "", onChange }) {
    const [mode, setMode] = useState("visual");
    const editorRef = useRef(null);

    useEffect(() => {
        if (mode !== "visual" || !editorRef.current) return;
        if (editorRef.current.innerHTML !== (value || "")) {
            editorRef.current.innerHTML = value || "";
        }
    }, [mode, value]);

    const emitVisual = () => onChange?.(editorRef.current?.innerHTML || "");
    const command = (name, commandValue = null) => {
        editorRef.current?.focus();
        run(name, commandValue);
        emitVisual();
    };

    return (
        <div className="mt-1 overflow-hidden rounded-xl border border-slate-200 bg-white" dir="rtl">
            <div className="flex flex-wrap items-center justify-between gap-2 border-b bg-slate-50 p-2">
                <div className="flex gap-1">
                    <button type="button" onClick={() => setMode("visual")} className={`rounded-lg px-3 py-2 text-xs font-black ${mode === "visual" ? "bg-violet-700 text-white" : "border bg-white"}`}><Eye className="ml-1 inline" />محرر مرئي</button>
                    <button type="button" onClick={() => setMode("source")} className={`rounded-lg px-3 py-2 text-xs font-black ${mode === "source" ? "bg-slate-900 text-white" : "border bg-white"}`}><Code className="ml-1 inline" />HTML متقدم</button>
                </div>
                {mode === "visual" && <div className="flex gap-1">
                    <button type="button" title="عريض" onClick={() => command("bold")} className="rounded-lg border bg-white p-2"><TextB /></button>
                    <button type="button" title="مائل" onClick={() => command("italic")} className="rounded-lg border bg-white p-2"><TextItalic /></button>
                    <button type="button" title="قائمة نقطية" onClick={() => command("insertUnorderedList")} className="rounded-lg border bg-white p-2"><ListBullets /></button>
                    <button type="button" title="قائمة رقمية" onClick={() => command("insertOrderedList")} className="rounded-lg border bg-white p-2"><ListNumbers /></button>
                </div>}
            </div>
            {mode === "visual" ? (
                <div
                    ref={editorRef}
                    contentEditable
                    suppressContentEditableWarning
                    onInput={emitVisual}
                    onBlur={emitVisual}
                    className="min-h-64 max-h-[520px] overflow-auto p-4 text-sm leading-8 text-slate-950 outline-none [&_img]:h-auto [&_img]:max-w-full [&_table]:max-w-full"
                />
            ) : (
                <textarea
                    rows={14}
                    value={value}
                    onChange={(event) => onChange?.(event.target.value)}
                    className="w-full resize-y p-4 font-mono text-xs leading-6 text-slate-950 outline-none"
                    dir="ltr"
                />
            )}
        </div>
    );
}
