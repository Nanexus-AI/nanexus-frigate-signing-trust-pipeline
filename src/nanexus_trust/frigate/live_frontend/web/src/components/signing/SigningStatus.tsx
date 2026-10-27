import { useCallback, useSyncExternalStore } from "react";
import { isDesktop } from "react-device-detect";
import {
  PlaybackKey,
  SigningClient,
  signingClient,
  SigningResult,
} from "@/api/live-signing";

const description = {
  ACTIVE: "Recent supported signing evidence observed",
  DEGRADED: "Signing evidence overdue while media continues",
  UNKNOWN: "Signing status unavailable or insufficient evidence",
};
const color = {
  ACTIVE: "text-green-200",
  DEGRADED: "text-amber-200",
  UNKNOWN: "text-slate-200",
};

export function SigningLabel({ result }: { result: SigningResult }) {
  const text = `Signing Status: ${result.status}`;
  return (
    <span
      role="status"
      aria-live="polite"
      aria-atomic="true"
      aria-label={text}
      title={description[result.status]}
      className={`pointer-events-none rounded bg-black/90 px-2 py-1 text-xs font-medium shadow ${color[result.status]}`}
    >
      {text}
    </span>
  );
}

function SubscribedStatus({
  camera,
  stream,
  context,
  client,
}: PlaybackKey & { client: SigningClient }) {
  const subscribe = useCallback(
    (notify: () => void) => client.subscribe({ camera, stream, context }, notify),
    [camera, stream, context, client],
  );
  const snapshot = useCallback(
    () => client.snapshot({ camera, stream, context }),
    [camera, stream, context, client],
  );
  const result = useSyncExternalStore(subscribe, snapshot, snapshot);
  return <SigningLabel result={result} />;
}

/** Opt in only at supported desktop Live seams; never mounted for other players. */
export default function SigningStatus({
  client = signingClient,
  ...props
}: PlaybackKey & { client?: SigningClient }) {
  return isDesktop ? <SubscribedStatus {...props} client={client} /> : null;
}
