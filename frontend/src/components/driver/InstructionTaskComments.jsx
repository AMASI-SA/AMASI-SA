import { useEffect, useRef, useState } from "react";
import api from "../../lib/api";

export default function InstructionTaskComments({ instructionId, taskId }) {
  const [items, setItems] = useState([]), [body, setBody] = useState(""), [error, setError] = useState(""), [busy, setBusy] = useState(false);
  const pending = useRef(null);
  const endpoint = `/order-tracking-notes/instructions/${encodeURIComponent(instructionId)}/tasks/${encodeURIComponent(taskId)}/comments`;
  useEffect(() => {
    let live = true; setItems([]); setBody(""); pending.current = null;
    const read = async () => { try { const { data } = await api.get(endpoint); if (live) { setItems(data.items || []); setError(""); } } catch { if (live) setError("تعذر تحميل التعليقات."); } };
    read(); const interval = window.setInterval(read, 30000);
    return () => { live = false; window.clearInterval(interval); };
  }, [endpoint]);
  async function send() {
    if (busy || !body.trim()) return;
    setBusy(true); setError("");
    if (!pending.current || pending.current.body !== body.trim()) pending.current = { body: body.trim(), client_request_id: crypto.randomUUID() };
    try {
      await api.post(endpoint, pending.current);
      setBody(""); pending.current = null;
      const { data } = await api.get(endpoint); setItems(data.items || []);
    } catch { setError("تعذر تأكيد إرسال التعليق. يمكنك إعادة المحاولة دون تكراره."); }
    finally { setBusy(false); }
  }
  return <section className="mt-2 rounded border bg-white p-2" aria-label="تعليقات مهمة المنتج"><h4 className="text-xs font-bold">التواصل مع صاحب الطلب والموظف المسؤول</h4>{items.map((item) => <div key={item.id} className="mt-2 rounded bg-slate-50 p-2 text-xs"><b>{item.author_name || "المسؤول"}</b><p className="whitespace-pre-wrap">{item.body}</p></div>)}{error && <p role="alert" className="text-xs text-rose-700">{error}</p>}<textarea aria-label="تعليق على مهمة المنتج" maxLength={2000} disabled={busy} value={body} onChange={(event) => setBody(event.target.value)} className="mt-2 w-full rounded border p-2 text-sm" /><button type="button" disabled={busy || !body.trim()} onClick={send} className="rounded border px-3 py-1 text-xs">إرسال التعليق</button><p className="mt-1 text-xs text-slate-500">التعليق لا يؤكد التنفيذ ولا يفتح المسار.</p></section>;
}
