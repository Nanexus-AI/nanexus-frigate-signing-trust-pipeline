import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import SigningStatus from "@/components/signing/SigningStatus";
import {
  monitorPage,
  type MonitorCamera,
} from "@/components/signing/monitor-layout";
import {
  createSnapshotSession,
  loadLatestStill,
  type SnapshotLoad,
  type SnapshotSession,
} from "@/components/signing/monitor-snapshot";
import type { SigningClient } from "@/api/live-signing";

type SigningMonitorProps = {
  cameras: readonly MonitorCamera[];
  client?: SigningClient;
  initialPage?: number;
  onSelect?: (cameraId: string) => void;
  loadSnapshot?: SnapshotLoad;
};

/** Count-based desktop grid. Empty slots are not cameras and do not subscribe. */
export default function SigningMonitor({
  cameras,
  client,
  initialPage = 0,
  onSelect,
  loadSnapshot = loadLatestStill,
}: SigningMonitorProps) {
  const [page, setPage] = useState(initialPage);
  const view = monitorPage(cameras, page);
  const sessionRef = useRef<SnapshotSession | null>(null);
  if (sessionRef.current === null) {
    sessionRef.current = createSnapshotSession(loadSnapshot);
  }
  const session = sessionRef.current;

  useEffect(() => {
    return () => session.close();
  }, [session]);

  useEffect(() => {
    if (view.page !== page) setPage(view.page);
  }, [view.page, page]);

  const membership = cameras.map((camera) => camera.id).join("\n");
  useEffect(() => {
    const live = new Set(membership.split("\n").filter(Boolean));
    for (const id of session.ids()) {
      if (!live.has(id)) session.drop(id);
    }
  }, [membership, session]);

  const visibleKey = view.tiles.map((camera) => camera?.id ?? "").join("\n");
  useEffect(() => {
    for (const id of visibleKey.split("\n")) {
      if (id) session.appear(id);
    }
  }, [visibleKey, session]);

  if (view.pageCount === 0) {
    return (
      <p className="px-4 py-8 text-sm text-foreground" role="status">
        No authorized cameras
      </p>
    );
  }

  return (
    <div
      className="flex h-full min-h-0 flex-col gap-2 px-2"
      data-monitor-page={view.page}
    >
      {view.pageCount > 1 && (
        <div className="flex shrink-0 items-center gap-2 text-sm text-foreground">
          <button
            type="button"
            className="rounded border border-border px-2 py-1 disabled:opacity-40"
            onClick={() => setPage((current) => Math.max(0, current - 1))}
            disabled={view.page === 0}
          >
            Previous page
          </button>
          <span>
            Page {view.page + 1} of {view.pageCount}
          </span>
          <button
            type="button"
            className="rounded border border-border px-2 py-1 disabled:opacity-40"
            onClick={() =>
              setPage((current) => Math.min(view.pageCount - 1, current + 1))
            }
            disabled={view.page === view.pageCount - 1}
          >
            Next page
          </button>
        </div>
      )}
      <div
        className="grid min-h-0 flex-1 gap-2"
        style={{
          gridTemplateColumns: `repeat(${view.columns}, minmax(0, 1fr))`,
          gridTemplateRows: `repeat(${view.columns}, minmax(0, 1fr))`,
        }}
      >
        {view.tiles.map((camera, index) =>
          camera ? (
            <article
              key={camera.id}
              data-camera-id={camera.id}
              data-camera-label={camera.label}
              className="relative h-full min-h-0 min-w-0 overflow-hidden rounded-lg bg-black"
            >
              <button
                type="button"
                className="absolute inset-0 z-10"
                aria-label={`Open ${camera.label}`}
                onClick={() => onSelect?.(camera.id)}
              />
              <CameraStill cameraId={camera.id} session={session} />
              <span className="pointer-events-none absolute left-2 top-2 z-20 max-w-[80%] truncate rounded bg-black/70 px-2 py-0.5 text-xs text-white">
                {camera.label}
              </span>
              <div
                className="pointer-events-none absolute bottom-2 left-2 z-20"
                data-signing-overlay="true"
              >
                {camera.stream ? (
                  <SigningStatus
                    client={client}
                    camera={camera.id}
                    stream={camera.stream}
                    context="grid"
                  />
                ) : (
                  <span
                    className="pointer-events-none rounded bg-black/90 px-2 py-1 text-xs font-medium text-slate-100 shadow"
                    data-signing="not-configured"
                  >
                    Signing: not configured
                  </span>
                )}
              </div>
            </article>
          ) : (
            <div
              key={`empty-${view.page}-${index}`}
              data-empty-slot="true"
              className="h-full min-h-0 rounded-lg border border-dashed border-border bg-background"
            />
          ),
        )}
      </div>
    </div>
  );
}

function CameraStill({
  cameraId,
  session,
}: {
  cameraId: string;
  session: SnapshotSession;
}) {
  const record = useSyncExternalStore(
    session.subscribe,
    () => session.lookup(cameraId),
    () => session.lookup(cameraId),
  );
  if (record.state === "ready") {
    return (
      <img
        src={record.url}
        alt=""
        className="pointer-events-none absolute inset-0 size-full object-contain"
        data-snapshot="ready"
      />
    );
  }
  if (record.state === "unavailable") {
    return (
      <p
        className="pointer-events-none flex size-full items-center justify-center px-3 text-center text-xs text-slate-200"
        data-snapshot="unavailable"
      >
        Snapshot unavailable
      </p>
    );
  }
  return <div className="size-full" data-snapshot="pending" />;
}
