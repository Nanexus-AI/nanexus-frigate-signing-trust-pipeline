/** Authorized-camera layout for the desktop Live monitor. No network I/O. */

export type MonitorCamera = {
  id: string;
  label: string;
  /** Playback stream. Null means signing is not configured for this camera. */
  stream: string | null;
};

export type ConfiguredCamera = {
  id: string;
  enabled: boolean;
  label: string;
  stream: string | null;
  replay: boolean;
};

export function gridGeometry(count: number): {
  columns: 1 | 2 | 3 | 4;
  pageSize: number;
} | null {
  if (!Number.isInteger(count) || count <= 0) return null;
  if (count === 1) return { columns: 1, pageSize: 1 };
  if (count <= 4) return { columns: 2, pageSize: 4 };
  if (count <= 9) return { columns: 3, pageSize: 9 };
  return { columns: 4, pageSize: 16 };
}

export function selectAuthorizedCameras(input: {
  authenticated: boolean;
  allowedIds: readonly string[];
  configured: readonly ConfiguredCamera[];
}): MonitorCamera[] {
  const byId = new Map<string, ConfiguredCamera>();
  for (const camera of input.configured) {
    if (!camera.id || camera.replay || !camera.enabled || byId.has(camera.id))
      continue;
    byId.set(camera.id, camera);
  }
  const ids = input.authenticated
    ? input.allowedIds.filter((id, index) => input.allowedIds.indexOf(id) === index)
    : [...byId.keys()];
  return ids
    .filter((id) => byId.has(id))
    .sort((a, b) => (a < b ? -1 : a > b ? 1 : 0))
    .map((id) => {
      const camera = byId.get(id)!;
      return {
        id,
        label: camera.label || id,
        stream: camera.stream && camera.stream.length > 0 ? camera.stream : null,
      };
    });
}

export function monitorPage(cameras: readonly MonitorCamera[], requestedPage: number) {
  const geometry = gridGeometry(cameras.length);
  if (!geometry) {
    return { page: 0, pageCount: 0, columns: 0, tiles: [] as (MonitorCamera | null)[] };
  }
  const pageCount = Math.ceil(cameras.length / geometry.pageSize);
  const page = Math.min(Math.max(0, requestedPage), pageCount - 1);
  const start = page * geometry.pageSize;
  const occupied = cameras.slice(start, start + geometry.pageSize);
  const tiles: (MonitorCamera | null)[] = occupied.slice();
  while (tiles.length < geometry.pageSize) tiles.push(null);
  return { page, pageCount, columns: geometry.columns, tiles };
}
