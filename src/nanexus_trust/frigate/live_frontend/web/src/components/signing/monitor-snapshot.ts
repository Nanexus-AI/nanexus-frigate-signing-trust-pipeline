import { baseUrl } from "@/api/baseUrl";

export type SnapshotRecord =
  | { readonly state: "idle" }
  | { readonly state: "pending"; readonly generation: number }
  | { readonly state: "ready"; readonly generation: number; readonly url: string }
  | { readonly state: "unavailable"; readonly generation: number };

const IDLE: SnapshotRecord = { state: "idle" };

export type SnapshotLoad = (cameraId: string) => Promise<string | null>;

export type SnapshotSession = {
  appear(cameraId: string): void;
  lookup(cameraId: string): SnapshotRecord;
  subscribe(listener: () => void): () => void;
  ids(): string[];
  drop(cameraId: string): void;
  close(): void;
};

function release(url: string | undefined) {
  if (
    url &&
    url.startsWith("blob:") &&
    typeof URL.revokeObjectURL === "function"
  ) {
    URL.revokeObjectURL(url);
  }
}

/** Memory-only cache for one mounted monitor. Each camera id is loaded at most once. */
export function createSnapshotSession(load: SnapshotLoad): SnapshotSession {
  const records = new Map<string, Exclude<SnapshotRecord, { state: "idle" }>>();
  const listeners = new Set<() => void>();
  let closed = false;
  let generation = 0;

  const emit = () => {
    for (const listener of listeners) listener();
  };

  return {
    appear(cameraId) {
      if (closed || records.has(cameraId)) return;
      const token = ++generation;
      records.set(cameraId, { state: "pending", generation: token });
      emit();
      load(cameraId).then(
        (url) => {
          const current = records.get(cameraId);
          if (closed || !current || current.generation !== token) {
            release(url ?? undefined);
            return;
          }
          records.set(
            cameraId,
            url
              ? { state: "ready", generation: token, url }
              : { state: "unavailable", generation: token },
          );
          emit();
        },
        () => {
          const current = records.get(cameraId);
          if (closed || !current || current.generation !== token) return;
          records.set(cameraId, { state: "unavailable", generation: token });
          emit();
        },
      );
    },
    lookup(cameraId) {
      return records.get(cameraId) ?? IDLE;
    },
    subscribe(listener) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    ids() {
      return [...records.keys()];
    },
    drop(cameraId) {
      const current = records.get(cameraId);
      if (!current) return;
      if (current.state === "ready") release(current.url);
      records.delete(cameraId);
      emit();
    },
    close() {
      closed = true;
      for (const record of records.values()) {
        if (record.state === "ready") release(record.url);
      }
      records.clear();
    },
  };
}

/** One GET of Frigate's existing latest-frame route. No refresh and no retry live here. */
export async function loadLatestStill(cameraId: string): Promise<string | null> {
  const response = await fetch(
    `${baseUrl}api/${encodeURIComponent(cameraId)}/latest.jpg?store=0`,
    { method: "GET", cache: "no-store", credentials: "same-origin" },
  );
  if (!response.ok) return null;
  const type = response.headers.get("content-type") ?? "";
  if (!type.startsWith("image/")) return null;
  const blob = await response.blob();
  if (!blob.size) return null;
  return URL.createObjectURL(blob);
}
