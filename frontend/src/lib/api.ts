// Thin API client. The backend URL and optional access token live in localStorage (never AI provider keys).
const LS = { url: "ai3d.apiUrl", token: "ai3d.apiToken" };

function safeGet(k: string): string {
  try { return localStorage.getItem(k) || ""; } catch { return ""; }
}
export function apiBase(): string {
  return (safeGet(LS.url) || (import.meta.env.VITE_API_URL as string) || "").replace(/\/$/, "");
}
export function apiToken(): string { return safeGet(LS.token); }
export function setConnection(url: string, token: string) {
  try { localStorage.setItem(LS.url, url); localStorage.setItem(LS.token, token); } catch { /* ignore */ }
}
export function absUrl(path: string): string { return path.startsWith("http") ? path : apiBase() + path; }
export function wsUrl(path: string): string {
  const base = apiBase() || window.location.origin;
  const u = new URL(base + path);
  u.protocol = u.protocol === "https:" ? "wss:" : "ws:";
  if (apiToken()) u.searchParams.set("token", apiToken());
  return u.toString();
}
function headers(json = true): HeadersInit {
  const h: Record<string, string> = {};
  if (json) h["content-type"] = "application/json";
  if (apiToken()) h["authorization"] = `Bearer ${apiToken()}`;
  return h;
}
export class ApiError extends Error { status: number; constructor(m: string, s: number) { super(m); this.status = s; } }

async function handle<T>(r: Response): Promise<T> {
  if (!r.ok) {
    let msg = r.statusText;
    try { const j = await r.json(); msg = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail ?? j); } catch { /* */ }
    throw new ApiError(msg, r.status);
  }
  const ct = r.headers.get("content-type") || "";
  return (ct.includes("json") ? r.json() : (r as unknown)) as Promise<T>;
}
export const api = {
  get: <T,>(p: string) => fetch(absUrl(p), { headers: headers(false) }).then((r) => handle<T>(r)),
  post: <T,>(p: string, body?: unknown) =>
    fetch(absUrl(p), { method: "POST", headers: headers(), body: body === undefined ? undefined : JSON.stringify(body) }).then((r) => handle<T>(r)),
  patch: <T,>(p: string, body: unknown) => fetch(absUrl(p), { method: "PATCH", headers: headers(), body: JSON.stringify(body) }).then((r) => handle<T>(r)),
  put: <T,>(p: string, body: unknown) => fetch(absUrl(p), { method: "PUT", headers: headers(), body: JSON.stringify(body) }).then((r) => handle<T>(r)),
  del: <T,>(p: string) => fetch(absUrl(p), { method: "DELETE", headers: headers(false) }).then((r) => handle<T>(r)),
  upload: <T,>(p: string, form: FormData) => fetch(absUrl(p), { method: "POST", headers: headers(false), body: form }).then((r) => handle<T>(r)),
  blob: (p: string) => fetch(absUrl(p), { headers: headers(false) }).then(async (r) => { if (!r.ok) throw new ApiError(r.statusText, r.status); return r.blob(); }),
  arrayBuffer: (p: string) => fetch(absUrl(p), { headers: headers(false) }).then(async (r) => { if (!r.ok) throw new ApiError(r.statusText, r.status); return r.arrayBuffer(); }),
};
export async function download(path: string, filename: string) {
  const b = await api.blob(path);
  const a = document.createElement("a");
  a.href = URL.createObjectURL(b);
  a.download = filename;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 4000);
}
