import { useEffect, useRef, useState } from "react";
import { Camera, CameraSlash } from "@phosphor-icons/react";

export default function BarcodeCameraScanner({ onDetected, active = true, label = "تشغيل الكاميرا" }) {
  const videoRef = useRef(null);
  const streamRef = useRef(null);
  const detectorRef = useRef(null);
  const timerRef = useRef(null);
  const lastRef = useRef({ value: "", at: 0 });
  const [running, setRunning] = useState(false);
  const [unsupported, setUnsupported] = useState(false);
  const [error, setError] = useState("");

  async function stop() {
    if (timerRef.current) window.clearTimeout(timerRef.current);
    timerRef.current = null;
    streamRef.current?.getTracks?.().forEach((track) => track.stop());
    streamRef.current = null;
    if (videoRef.current) videoRef.current.srcObject = null;
    setRunning(false);
  }

  async function start() {
    setError("");
    if (!("BarcodeDetector" in window) || !navigator.mediaDevices?.getUserMedia) {
      setUnsupported(true);
      return;
    }
    try {
      detectorRef.current = new window.BarcodeDetector({
        formats: ["code_128", "code_39", "ean_13", "ean_8", "qr_code", "data_matrix"],
      });
      const stream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: { ideal: "environment" } },
        audio: false,
      });
      streamRef.current = stream;
      if (videoRef.current) {
        videoRef.current.srcObject = stream;
        await videoRef.current.play();
      }
      setRunning(true);
    } catch (err) {
      setError(err?.message || "تعذر تشغيل الكاميرا");
      await stop();
    }
  }

  useEffect(() => {
    if (!running || !active) return undefined;
    let cancelled = false;
    const scan = async () => {
      if (cancelled || !videoRef.current || !detectorRef.current) return;
      try {
        const codes = await detectorRef.current.detect(videoRef.current);
        const value = String(codes?.[0]?.rawValue || "").trim();
        const now = Date.now();
        if (value && (value !== lastRef.current.value || now - lastRef.current.at > 2500)) {
          lastRef.current = { value, at: now };
          onDetected?.(value);
        }
      } catch {
        // A transient detector frame error should not terminate the camera.
      }
      timerRef.current = window.setTimeout(scan, 450);
    };
    scan();
    return () => {
      cancelled = true;
      if (timerRef.current) window.clearTimeout(timerRef.current);
    };
  }, [running, active, onDetected]);

  useEffect(() => () => { stop(); }, []);

  return (
    <div className="rounded-2xl border bg-slate-950 p-3 text-white">
      <div className="overflow-hidden rounded-xl bg-black">
        <video ref={videoRef} playsInline muted className={`aspect-video w-full object-cover ${running ? "block" : "hidden"}`} />
        {!running && <div className="flex aspect-video items-center justify-center text-center text-sm font-bold text-slate-300">الكاميرا متوقفة</div>}
      </div>
      <div className="mt-3 flex gap-2">
        {!running ? (
          <button type="button" onClick={start} className="flex flex-1 items-center justify-center gap-2 rounded-xl bg-white px-3 py-3 text-sm font-black text-slate-950"><Camera size={20} />{label}</button>
        ) : (
          <button type="button" onClick={stop} className="flex flex-1 items-center justify-center gap-2 rounded-xl bg-slate-800 px-3 py-3 text-sm font-black"><CameraSlash size={20} />إيقاف الكاميرا</button>
        )}
      </div>
      {unsupported && <div className="mt-2 rounded-lg bg-amber-100 px-3 py-2 text-xs font-bold text-amber-950">هذا المتصفح لا يدعم قراءة الباركود مباشرة. استخدم حقل الباركود اليدوي أو متصفح Chrome حديث على الجوال.</div>}
      {error && <div className="mt-2 rounded-lg bg-rose-100 px-3 py-2 text-xs font-bold text-rose-950">{error}</div>}
    </div>
  );
}
