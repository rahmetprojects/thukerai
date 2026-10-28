/*
 src/lib/recorder.ts - record the microphone while the button is held (for server transcription)
 =================================================================================================
 Used only when the server says it can transcribe (/api/health -> stt: "gemini"). The recording is sent to
 /api/transcribe on release. If the browser cannot record (no MediaRecorder, permission refused), the caller
 falls back to the browser's own speech recognition.
*/
export interface Recording { stop(): Promise<Blob | null>; cancel(): void }

export const recordingSupported = (): boolean =>
  typeof window !== "undefined" && "MediaRecorder" in window && !!navigator.mediaDevices?.getUserMedia;

export async function startRecording(): Promise<Recording> {
  const stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } });
  const type = ["audio/webm;codecs=opus", "audio/webm", "audio/ogg;codecs=opus", "audio/mp4"].find((t) => MediaRecorder.isTypeSupported(t));
  const rec = new MediaRecorder(stream, type ? { mimeType: type } : undefined);
  const chunks: Blob[] = [];
  rec.ondataavailable = (e) => { if (e.data.size) chunks.push(e.data); };
  rec.start(250);
  const release = () => stream.getTracks().forEach((t) => t.stop());
  return {
    stop: () => new Promise((resolve) => {
      rec.onstop = () => { release(); resolve(chunks.length ? new Blob(chunks, { type: rec.mimeType || "audio/webm" }) : null); };
      if (rec.state !== "inactive") rec.stop(); else { release(); resolve(null); }
    }),
    cancel: () => { try { rec.stop(); } catch { /* already stopped */ } release(); },
  };
}
