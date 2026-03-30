#!/usr/bin/env bun
/**
 * Local Hebrew voice server for Claude Code (macOS).
 * Uses Apple's native SFSpeechRecognizer for on-device transcription.
 * Mimics Anthropic's voice_stream WebSocket protocol.
 */

import { writeFileSync, unlinkSync } from "fs";
import { tmpdir } from "os";
import { join } from "path";

const PORT = parseInt(process.env.VOICE_SERVER_PORT || "19876");
const TRANSCRIBE_APP = join(import.meta.dir, "Transcribe.app");

function wavHeader(dataLen) {
  const b = Buffer.alloc(44);
  b.write("RIFF", 0); b.writeUInt32LE(36 + dataLen, 4);
  b.write("WAVE", 8); b.write("fmt ", 12);
  b.writeUInt32LE(16, 16); b.writeUInt16LE(1, 20); b.writeUInt16LE(1, 22);
  b.writeUInt32LE(16000, 24); b.writeUInt32LE(32000, 28);
  b.writeUInt16LE(2, 32); b.writeUInt16LE(16, 34);
  b.write("data", 36); b.writeUInt32LE(dataLen, 40);
  return b;
}

async function transcribe(chunks, language) {
  const raw = Buffer.concat(chunks);
  if (raw.length === 0) return "";

  const wav = Buffer.concat([wavHeader(raw.length), raw]);
  const tmp = join(tmpdir(), `hv-${Date.now()}.wav`);
  const out = join(tmpdir(), `hv-${Date.now()}.txt`);
  const locale = language.includes("-") ? language : `${language}-IL`;

  writeFileSync(tmp, wav);
  console.log(`[voice] ${(raw.length / 32000).toFixed(1)}s audio → Apple STT (${locale})`);

  try {
    const proc = Bun.spawn(["open", "-W", TRANSCRIBE_APP, "--args", tmp, locale, out],
      { stdout: "ignore", stderr: "ignore" });
    await proc.exited;
    return (await Bun.file(out).text().catch(() => "")).trim();
  } finally {
    try { unlinkSync(tmp); } catch {}
    try { unlinkSync(out); } catch {}
  }
}

Bun.serve({
  port: PORT,
  fetch(req, server) {
    const url = new URL(req.url);
    if (url.pathname === "/api/ws/speech_to_text/voice_stream") {
      // Always use Hebrew — ignores Claude Code's language param
      // so no binary patch is needed
      const language = "he";
      return server.upgrade(req, { data: { language, chunks: [], closed: false } })
        ? undefined
        : new Response("Upgrade failed", { status: 500 });
    }
    return new Response("OK");
  },
  websocket: {
    open(ws) { console.log(`[voice] Connected (${ws.data.language})`); },
    async message(ws, msg) {
      if (typeof msg === "string") {
        let m; try { m = JSON.parse(msg); } catch { return; }
        if (m.type === "KeepAlive") return;
        if (m.type === "CloseStream" && !ws.data.closed) {
          ws.data.closed = true;
          ws.send(JSON.stringify({ type: "TranscriptText", data: "" }));
          try {
            const text = await transcribe(ws.data.chunks, ws.data.language);
            console.log(`[voice] "${text}"`);
            if (text) ws.send(JSON.stringify({ type: "TranscriptText", data: text }));
            ws.send(JSON.stringify({ type: "TranscriptEndpoint" }));
          } catch (e) {
            console.error(`[voice] Error: ${e.message}`);
            ws.send(JSON.stringify({ type: "TranscriptError", description: e.message }));
          }
        }
        return;
      }
      if (!ws.data.closed) ws.data.chunks.push(Buffer.from(msg));
    },
    close(ws) { ws.data.chunks = []; },
  },
});

console.log(`[voice] Hebrew voice server on ws://127.0.0.1:${PORT} (Apple STT)`);
