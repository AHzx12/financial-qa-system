/**
 * API client — SSE streaming + session management.
 */

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "/api/backend";

export interface RoutingInfo {
  type: "routing";
  category: "market_data" | "knowledge" | "general" | "compound";
  ticker?: string;
  company_name?: string;
  session_id?: string;
}

export interface SourcesInfo {
  type: "sources";
  content: Record<string, unknown>;
}

export type SSEEvent =
  | RoutingInfo
  | { type: "text"; content: string }
  | { type: "status"; content: string }
  | SourcesInfo
  | { type: "error"; content: string }
  | { type: "done" };

// ---- Unified fetch wrapper ----

async function apiFetch<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, init);
  if (!res.ok) {
    const detail = await res.text().catch(() => res.statusText);
    throw new Error(`API ${res.status}: ${detail}`);
  }
  return res.json();
}

// ---- Session CRUD ----

export async function createSession(title?: string): Promise<{ id: string; title: string }> {
  return apiFetch(`${API_BASE}/sessions`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title: title || "New chat" }),
  });
}

export async function listSessions(limit = 20): Promise<Array<{
  id: string; title: string; created_at: string; updated_at: string;
}>> {
  return apiFetch(`${API_BASE}/sessions?limit=${limit}`);
}

export async function getSession(id: string) {
  try {
    return await apiFetch(`${API_BASE}/sessions/${id}`);
  } catch {
    return null;
  }
}

export async function deleteSession(id: string): Promise<boolean> {
  try {
    await apiFetch(`${API_BASE}/sessions/${id}`, { method: "DELETE" });
    return true;
  } catch {
    return false;
  }
}

// ---- Chat streaming ----

export async function streamChat(
  message: string,
  sessionId: string | null,
  callbacks: {
    onRouting?: (info: RoutingInfo) => void;
    onChunk?: (text: string) => void;
    onStatus?: (status: string) => void;
    onSources?: (sources: Record<string, unknown>) => void;
    onDone?: () => void;
    onError?: (error: string) => void;
  }
) {
  let response: Response;
  try {
    const body: Record<string, unknown> = { message };
    if (sessionId) {
      body.session_id = sessionId;
    }
    response = await fetch(`${API_BASE}/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  } catch {
    callbacks.onError?.("Cannot connect to backend.");
    callbacks.onDone?.();
    return;
  }

  if (!response.ok) {
    callbacks.onError?.(`HTTP ${response.status}: ${response.statusText}`);
    callbacks.onDone?.();
    return;
  }

  const reader = response.body?.getReader();
  if (!reader) { callbacks.onError?.("No body"); callbacks.onDone?.(); return; }

  const decoder = new TextDecoder();
  let buffer = "";

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop() || "";

      for (const line of lines) {
        if (!line.startsWith("data: ")) continue;
        const jsonStr = line.slice(6).trim();
        if (!jsonStr) continue;
        try {
          const event: SSEEvent = JSON.parse(jsonStr);
          switch (event.type) {
            case "routing": callbacks.onRouting?.(event as RoutingInfo); break;
            case "status": callbacks.onStatus?.(event.content); break;
            case "text": callbacks.onChunk?.(event.content); break;
            case "sources": callbacks.onSources?.((event as SourcesInfo).content); break;
            case "error": callbacks.onError?.(event.content); break;
            case "done": callbacks.onDone?.(); return;
          }
        } catch { /* skip malformed */ }
      }
    }
  } finally {
    reader.releaseLock();
  }
  callbacks.onDone?.();
}