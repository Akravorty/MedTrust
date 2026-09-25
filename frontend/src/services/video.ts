// Video room for a teleconsult session.
//
// The room name comes from the session id, so the health worker and the doctor
// open the same URL. The server defaults to the public Jitsi Meet instance and
// can be pointed at a self-hosted Jitsi (or any Jitsi-compatible host) with
// VITE_VIDEO_BASE_URL. It is opened as its own browser tab, not embedded, because
// the public Jitsi server limits embedded meetings to a few minutes.
//
// A public server room is open to anyone who has the link. For real patient use,
// self-host with authentication.

const VIDEO_BASE = (
  (import.meta.env.VITE_VIDEO_BASE_URL as string | undefined)?.trim() || 'https://meet.jit.si'
).replace(/\/+$/, '');

export const videoRoomUrl = (sessionId: string): string =>
  `${VIDEO_BASE}/MedTrust-${encodeURIComponent(sessionId)}`;
