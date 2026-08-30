export interface Source {
  id: number;
  title: string;
  doi: string | null;
  created_at: string;
}

export interface Health {
  status: "ok";
  technology: Record<string, string>;
}

export interface ConvertedDocument {
  filename: string;
  format: string;
  markdown: string;
}

const apiBase = import.meta.env.VITE_API_URL ?? "http://127.0.0.1:8765";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${apiBase}${path}`, init);
  if (!response.ok) {
    throw new Error(`Litrev service returned ${response.status}`);
  }
  return response.json() as Promise<T>;
}

export function getHealth(): Promise<Health> {
  return request<Health>("/api/health");
}

export function getSources(): Promise<Source[]> {
  return request<Source[]>("/api/sources");
}

export function createSource(title: string): Promise<Source> {
  return request<Source>("/api/sources", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title }),
  });
}

export function convertDocument(file: File): Promise<ConvertedDocument> {
  const form = new FormData();
  form.append("document", file);
  return request<ConvertedDocument>("/api/documents/convert", {
    method: "POST",
    body: form,
  });
}
