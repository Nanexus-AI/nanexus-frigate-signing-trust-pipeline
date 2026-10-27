import { useEffect, useState } from "react";
import { createRoot, type Root } from "react-dom/client";
import { baseUrl } from "@/api/baseUrl";
import SigningMonitor from "@/components/signing/SigningMonitor";
import {
  selectAuthorizedCameras,
  type ConfiguredCamera,
  type MonitorCamera,
} from "@/components/signing/monitor-layout";
import { isReplayCamera } from "@/utils/cameraUtil";
import type { FrigateConfig } from "@/types/frigateConfig";

type Profile = {
  allowed_cameras?: string[];
};

async function readJson<T>(path: string): Promise<T> {
  const response = await fetch(`${baseUrl}${path}`, {
    credentials: "same-origin",
    cache: "no-store",
  });
  if (!response.ok) throw new Error("unavailable");
  return response.json() as Promise<T>;
}

function camerasFrom(config: FrigateConfig, profile: Profile): MonitorCamera[] {
  const configured: ConfiguredCamera[] = Object.values(config.cameras ?? {}).map(
    (camera) => {
      const streams = camera.live?.streams ?? {};
      const streamKey = Object.keys(streams).sort()[0];
      return {
        id: camera.name,
        enabled: camera.enabled_in_config,
        label: camera.friendly_name || camera.name,
        stream: streamKey ? streams[streamKey] : null,
        replay: isReplayCamera(camera.name),
      };
    },
  );
  return selectAuthorizedCameras({
    authenticated: true,
    allowedIds: profile.allowed_cameras ?? [],
    configured,
  });
}

function NanexusLiveMonitor() {
  const [cameras, setCameras] = useState<MonitorCamera[] | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let live = true;
    Promise.all([
      readJson<FrigateConfig>("api/config"),
      readJson<Profile>("api/profile"),
    ])
      .then(([config, profile]) => {
        if (live) setCameras(camerasFrom(config, profile));
      })
      .catch(() => {
        if (live) setFailed(true);
      });
    return () => {
      live = false;
    };
  }, []);
  if (failed) {
    return (
      <p className="px-4 py-8 text-sm text-foreground" role="status">
        No authorized cameras
      </p>
    );
  }
  if (!cameras) {
    return (
      <p className="px-4 py-8 text-sm text-foreground" role="status">
        Loading cameras
      </p>
    );
  }
  return <SigningMonitor cameras={cameras} />;
}

type LiveEntry = {
  mount(element: HTMLElement): () => void;
};

/** One mount point for the existing Nanexus Live destination. */
export function installNanexusLiveEntry() {
  const target = window as Window & { __nanexusLive?: LiveEntry };
  if (target.__nanexusLive) return;
  const roots = new Map<HTMLElement, Root>();
  target.__nanexusLive = {
    mount(element) {
      const previous = roots.get(element);
      previous?.unmount();
      const root = createRoot(element);
      roots.set(element, root);
      root.render(<NanexusLiveMonitor />);
      return () => {
        root.unmount();
        roots.delete(element);
      };
    },
  };
}
