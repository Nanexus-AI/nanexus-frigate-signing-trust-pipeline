import { baseUrl } from "./baseUrl";

export type PlaybackKey = {
  camera: string;
  stream: string;
  context: "grid" | "single";
};
export type SigningResult = {
  status: "ACTIVE" | "DEGRADED" | "UNKNOWN";
  reason: string;
  age_ms: string | null;
  observed_at: string | null;
  last_evidence_at: string | null;
  session_id: string | null;
  mapping_revision: string | null;
  logical_camera: string | null;
  rendition: string | null;
};
export const UNKNOWN: SigningResult = Object.freeze({
  status: "UNKNOWN",
  reason: "transport_unavailable",
  age_ms: null,
  observed_at: null,
  last_evidence_at: null,
  session_id: null,
  mapping_revision: null,
  logical_camera: null,
  rendition: null,
});
const identifier = /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$/;
export const playbackId = (key: PlaybackKey) =>
  JSON.stringify([key.camera, key.stream, key.context]);

export function parseSigningResult(value: unknown): SigningResult {
  if (!value || typeof value !== "object") throw Error("Unavailable status");
  const data = value as SigningResult;
  if (
    Object.keys(data).sort().join() !== Object.keys(UNKNOWN).sort().join() ||
    !["ACTIVE", "DEGRADED", "UNKNOWN"].includes(data.status) ||
    typeof data.reason !== "string" ||
    !identifier.test(data.reason)
  )
    throw Error("Unavailable status");
  for (const name of [
    "session_id",
    "mapping_revision",
    "logical_camera",
    "rendition",
  ] as const) {
    if (
      data[name] !== null &&
      (typeof data[name] !== "string" || !identifier.test(data[name]))
    )
      throw Error("Unavailable status");
  }
  for (const name of ["observed_at", "last_evidence_at"] as const) {
    const stamp = data[name];
    if (
      stamp !== null &&
      (typeof stamp !== "string" ||
        stamp.length > 40 ||
        !/(Z|[+-]\d{2}:\d{2})$/.test(stamp) ||
        !Number.isFinite(Date.parse(stamp)))
    )
      throw Error("Unavailable status");
  }
  if (
    data.age_ms !== null &&
    (typeof data.age_ms !== "string" ||
      !/^\d{1,20}(\.\d{1,9})?$/.test(data.age_ms))
  )
    throw Error("Unavailable status");
  if (
    data.status !== "UNKNOWN" &&
    [
      data.age_ms,
      data.observed_at,
      data.session_id,
      data.mapping_revision,
      data.logical_camera,
      data.rendition,
    ].some((x) => x === null)
  )
    throw Error("Unavailable status");
  return data;
}

async function requestStatus(
  key: PlaybackKey,
  signal: AbortSignal,
): Promise<SigningResult> {
  const query = new URLSearchParams(key);
  const response = await fetch(`${baseUrl}api/nanexus/live/status?${query}`, {
    credentials: "same-origin",
    cache: "no-store",
    redirect: "error",
    signal,
  });
  if (!response.ok) throw Error("Unavailable status");
  const body = await response.text();
  if (body.length > 4096) throw Error("Unavailable status");
  return parseSigningResult(JSON.parse(body));
}

type Entry = {
  key: PlaybackKey;
  value: SigningResult;
  listeners: Set<() => void>;
  generation: number;
  pending: boolean;
  next: number;
  received: number;
  controller?: AbortController;
  identity: string | null;
  retired: Set<string>;
  observed: number;
};

/** One bounded presentation-delivery scheduler, never a media/signing observer. */
export class SigningClient {
  readonly pollMs = 1000;
  readonly expiryMs = 2500;
  readonly timeoutMs = 2000;
  readonly maxEntries = 16;
  readonly maxConcurrent = 2;
  private entries = new Map<string, Entry>();
  private timer?: ReturnType<typeof setInterval>;
  private active = 0;

  constructor(
    private request = requestStatus,
    private now = () => performance.now(),
  ) {}

  snapshot(key: PlaybackKey): SigningResult {
    return this.entries.get(playbackId(key))?.value ?? UNKNOWN;
  }

  subscribe(key: PlaybackKey, listener: () => void) {
    if (
      ![key.camera, key.stream].every((part) => identifier.test(part)) ||
      !["grid", "single"].includes(key.context)
    )
      return () => {};
    const id = playbackId(key);
    let entry = this.entries.get(id);
    if (!entry) {
      if (this.entries.size >= this.maxEntries) return () => {};
      entry = {
        key: { ...key },
        value: UNKNOWN,
        listeners: new Set(),
        generation: 0,
        pending: false,
        next: 0,
        received: 0,
        identity: null,
        retired: new Set(),
        observed: -Infinity,
      };
      this.entries.set(id, entry);
    }
    const current = entry;
    const notify = () => listener();
    current.listeners.add(notify);
    if (!this.timer) this.timer = setInterval(() => this.pump(), 250);
    this.pump();
    return () => {
      current.listeners.delete(notify);
      if (!current.listeners.size) {
        current.generation++;
        current.controller?.abort();
        if (this.entries.get(id) === current) this.entries.delete(id);
      }
      if (!this.entries.size && this.timer) {
        clearInterval(this.timer);
        this.timer = undefined;
      }
    };
  }

  private publish(entry: Entry, value: SigningResult) {
    entry.value = value;
    for (const notify of entry.listeners) notify();
  }

  private pump() {
    const now = this.now();
    for (const entry of this.entries.values()) {
      if (
        entry.value.status !== "UNKNOWN" &&
        now - entry.received >= this.expiryMs
      ) {
        entry.generation++;
        entry.controller?.abort();
        entry.next = now;
        this.publish(entry, UNKNOWN);
      }
    }
    const due = [...this.entries.values()]
      .filter((e) => !e.pending && e.next <= now)
      .sort((a, b) => a.next - b.next);
    for (const entry of due) {
      if (this.active >= this.maxConcurrent) break;
      this.start(entry);
    }
  }

  private start(entry: Entry) {
    entry.pending = true;
    this.active++;
    const generation = ++entry.generation;
    const id = playbackId(entry.key);
    const controller = new AbortController();
    entry.controller = controller;
    let timeout: ReturnType<typeof setTimeout>;
    const deadline = new Promise<never>((_, reject) => {
      timeout = setTimeout(() => {
        controller.abort();
        reject(Error("Unavailable status"));
      }, this.timeoutMs);
    });
    const current = () =>
      this.entries.get(id) === entry && entry.generation === generation;
    Promise.race([
      Promise.resolve().then(() => this.request(entry.key, controller.signal)),
      deadline,
    ])
      .then((data) => {
        if (!current()) return;
        const result = parseSigningResult(data);
        const identity =
          result.session_id && result.mapping_revision
            ? JSON.stringify([
                result.session_id,
                result.mapping_revision,
                result.logical_camera,
                result.rendition,
              ])
            : null;
        const observed = result.observed_at
          ? Date.parse(result.observed_at)
          : null;
        if (
          (identity && entry.retired.has(identity)) ||
          (observed !== null && observed < entry.observed)
        )
          return;
        if (identity && entry.identity && identity !== entry.identity) {
          entry.retired.add(entry.identity);
          if (entry.retired.size > 8)
            entry.retired.delete(entry.retired.values().next().value!);
        }
        if (identity) entry.identity = identity;
        if (observed !== null) entry.observed = observed;
        entry.received = this.now();
        this.publish(entry, result);
      })
      .catch(() => {
        if (current()) this.publish(entry, UNKNOWN);
      })
      .finally(() => {
        clearTimeout(timeout);
        this.active--;
        entry.pending = false;
        entry.next = this.now() + this.pollMs;
        this.pump();
      });
  }

  dispose() {
    if (this.timer) clearInterval(this.timer);
    this.timer = undefined;
    for (const entry of this.entries.values()) {
      entry.generation++;
      entry.controller?.abort();
    }
    this.entries.clear();
  }
}

export const signingClient = new SigningClient();
