const BASE = import.meta.env.VITE_API_BASE ?? 'http://localhost:8000';
const API = `${BASE}/api/demo`;

export interface Tenant {
  projectId: string;
  projectName: string;
  projectDescription?: string;
  demoIcon?: string;
  demoSampleQuestions?: string[];
  projectInstruction?: string;
  projectGuardrails?: string;
  isEphemeral?: boolean;
  chunkCount: number;
  documents: string[];
}

export interface Candidate {
  topicName: string;
  chunkIndex: number;
  sourceFile: string;
  preview: string;
  vectorScore: number;
  keywordScore: number;
  rerankScore: number;
  selected: boolean;
}

export interface Citation {
  topicName: string;
  chunkIndex: number;
  sourceFile: string;
  text: string;
  pageStart?: number;
  pageEnd?: number;
  rerankScore?: number;
  vectorScore?: number;
  keywordScore?: number;
}

export interface Timings {
  embedQueryMs?: number;
  vectorSearchMs?: number;
  rerankMs?: number;
  generationMs?: number;
  totalMs?: number;
}

export interface Usage {
  model: string;
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  cost_usd: number;
}

export interface ChatResult {
  answer: string;
  images: string[];
  citations: Citation[];
  candidates: Candidate[];
  vectorOnlyOrder: string[];
  hybridOrder: string[];
  grounded: boolean;
  refused: boolean;
  timings: Timings;
  usage: Usage | null;
}

export interface CompareResult {
  query: string;
  ungrounded: { answer: string; latencyMs: number; usage: Usage | null; sources: string[] };
  grounded: { answer: string; latencyMs: number; usage: Usage | null; sources: string[] };
}

export interface Chunk {
  chunk_index: number;
  topic_name: string;
  text: string;
  word_count: number;
  page_start: number;
  page_end: number;
  source_file: string;
  associated_image_ids: string[];
  imageUrls: string[];
}

export interface ChunkDoc {
  sourceFile: string;
  chunkCount: number;
  totalWords: number;
  chunks: Chunk[];
}

export interface ProjectionPoint {
  x: number;
  y: number;
  topicName: string;
  chunkIndex: number;
  sourceFile: string;
  preview: string;
}

export interface EvalMetrics { hit1: number; hit3: number; mrr: number; total: number }
export interface EvalResult {
  available: boolean;
  message?: string;
  cases?: { query: string; expectedTopic: string; vectorRank: number | null; hybridRank: number | null; hit: boolean }[];
  vectorOnly?: EvalMetrics;
  hybrid?: EvalMetrics;
}

export interface UploadStage { stage: string; ms: number; detail: string }
export interface UploadResult {
  projectId: string;
  fileName: string;
  fileType: string;
  chunksCreated: number;
  imagesExtracted: number;
  totalPages: number;
  stages: UploadStage[];
  expiresAt: string;
}

export interface SourceDocument {
  sourceFile: string;
  fileId: string;
  chunkCount: number;
  totalWords: number;
  pages: number;
  extension: string;
  /** Browsers can render PDFs and images in a frame; Office formats they cannot. */
  canPreviewInline: boolean;
  url: string;
}

export interface Stats {
  chatModel: string;
  embeddingModel: string;
  embeddingDimensions: number;
  supportedFormats: string[];
  demoTenants: number;
  totalChunks: number;
}

/** Surfaces the backend's own error message rather than a generic HTTP failure. */
async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API}${path}`, init);
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try {
      const body = await res.text();
      const match = body.match(/<p>(.*?)<\/p>/s);
      if (match) detail = match[1].replace(/&#39;/g, "'").replace(/&quot;/g, '"').replace(/&amp;/g, '&');
      else if (body) detail = body.slice(0, 300);
    } catch { /* keep the status line */ }
    throw new Error(detail);
  }
  return res.json();
}

const postJson = (body: unknown): RequestInit => ({
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
});

export const api = {
  stats: () => req<Stats>('/stats'),
  tenants: () => req<{ tenants: Tenant[] }>('/tenants').then(r => r.tenants),
  chat: (query: string, projectId: string) => req<ChatResult>('/chat', postJson({ query, projectId })),
  compare: (query: string, projectId: string) => req<CompareResult>('/compare', postJson({ query, projectId })),
  chunks: (projectId: string) => req<{ totalChunks: number; documents: ChunkDoc[] }>(`/chunks/${projectId}`),
  projection: (projectId: string) =>
    req<{ points: ProjectionPoint[]; dimensions: number; chunkCount: number; message?: string }>(`/projection/${projectId}`),
  projectQuery: (projectId: string, query: string) =>
    req<{ x: number; y: number; query: string }>(`/projection/${projectId}/query`, postJson({ query })),
  evaluate: (projectId: string) => req<EvalResult>(`/eval/${projectId}`),
  documents: (projectId: string) =>
    req<{ documents: SourceDocument[] }>(`/documents/${projectId}`).then(r => r.documents),
  /** Absolute URL for an original file — used as an iframe/download target, not fetched. */
  documentUrl: (projectId: string, sourceFile: string) =>
    `${BASE}/api/demo/document/${projectId}/${encodeURIComponent(sourceFile)}`,
  upload: (file: File) => {
    const fd = new FormData();
    fd.append('file', file);
    return req<UploadResult>('/upload', { method: 'POST', body: fd });
  },
};
