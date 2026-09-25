/**
 * src/services/offlineQueue.ts
 *
 * A queue, not "offline mode". Say "queue" in the demo -- it is defensible;
 * "works offline" is not, because the risk model runs server-side.
 *
 * What this does: when a scan fails because the network is down, the request
 * is parked in IndexedDB instead of being lost, and replayed when the browser
 * fires `online`. Each queued item carries a client-generated Idempotency-Key
 * so a replay that the server already saw does not create a second batch.
 *
 * Deliberately dependency-free (raw IndexedDB, no idb-keyval) so this drops
 * into the existing project without touching package.json.
 */

const DB_NAME = 'meditrust-queue';
const STORE = 'pending-scans';
const DB_VERSION = 1;

export interface QueuedScan {
  id: string;              // also used as the Idempotency-Key
  batchId: string;
  queuedAt: string;
}

function openDb(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, DB_VERSION);
    req.onupgradeneeded = () => {
      const db = req.result;
      if (!db.objectStoreNames.contains(STORE)) {
        db.createObjectStore(STORE, { keyPath: 'id' });
      }
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

function tx<T>(mode: IDBTransactionMode, fn: (store: IDBObjectStore) => IDBRequest<T>): Promise<T> {
  return openDb().then(
    (db) =>
      new Promise<T>((resolve, reject) => {
        const store = db.transaction(STORE, mode).objectStore(STORE);
        const req = fn(store);
        req.onsuccess = () => resolve(req.result);
        req.onerror = () => reject(req.error);
      }),
  );
}

export async function enqueueScan(batchId: string): Promise<QueuedScan> {
  const item: QueuedScan = {
    id: crypto.randomUUID(),
    batchId,
    queuedAt: new Date().toISOString(),
  };
  await tx('readwrite', (s) => s.add(item));
  return item;
}

export async function listQueued(): Promise<QueuedScan[]> {
  return (await tx<QueuedScan[]>('readonly', (s) => s.getAll())) ?? [];
}

export async function removeQueued(id: string): Promise<void> {
  await tx('readwrite', (s) => s.delete(id) as unknown as IDBRequest<undefined>);
}

export async function queueDepth(): Promise<number> {
  return (await listQueued()).length;
}

/**
 * Replay every queued scan. `send` is injected rather than imported so this
 * module has no dependency on api.ts -- avoids a circular import, since
 * api.ts is what enqueues in the first place.
 *
 * An item is only removed once the server accepts it. A 4xx (the server
 * understood and refused -- e.g. batch genuinely not found) also removes it:
 * retrying forever would jam the queue behind one bad row. A network failure
 * leaves it in place for the next `online` event.
 */
export async function replayQueue(
  send: (batchId: string, idempotencyKey: string) => Promise<void>,
): Promise<{ succeeded: number; failed: number }> {
  const items = await listQueued();
  let succeeded = 0;
  let failed = 0;

  for (const item of items) {
    try {
      await send(item.batchId, item.id);
      await removeQueued(item.id);
      succeeded += 1;
    } catch (err) {
      const status = (err as { status?: number }).status;
      if (status && status >= 400 && status < 500) {
        await removeQueued(item.id);
        failed += 1;
      } else {
        failed += 1;
      }
    }
  }

  return { succeeded, failed };
}

/** Register a replay handler that fires whenever connectivity returns. */
export function onReconnect(handler: () => void): () => void {
  window.addEventListener('online', handler);
  return () => window.removeEventListener('online', handler);
}

// ═══════════════════════════════════════════════════════════════════════
// Generic action queue (care-access module) — same pattern as the scan
// queue above, but the payload is an arbitrary JSON-serializable object
// instead of a bare batchId, so it covers patient registration, triage
// submissions, etc. without inventing a second offline system.
// Bumps DB_VERSION so the upgrade handler also creates this store on a
// browser that already has the v1 scan-queue DB.
// ═══════════════════════════════════════════════════════════════════════
const ACTION_STORE = 'pending-actions';
const ACTION_DB_VERSION = 2;

export type QueuedActionKind = 'PATIENT_REGISTRATION' | 'TRIAGE_SUBMISSION';

export interface QueuedAction {
  id: string; // also used as the Idempotency-Key equivalent
  kind: QueuedActionKind;
  payload: Record<string, unknown>;
  queuedAt: string;
}

function openActionDb(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, ACTION_DB_VERSION);
    req.onupgradeneeded = () => {
      const db = req.result;
      if (!db.objectStoreNames.contains(STORE)) {
        db.createObjectStore(STORE, { keyPath: 'id' });
      }
      if (!db.objectStoreNames.contains(ACTION_STORE)) {
        db.createObjectStore(ACTION_STORE, { keyPath: 'id' });
      }
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

function actionTx<T>(mode: IDBTransactionMode, fn: (store: IDBObjectStore) => IDBRequest<T>): Promise<T> {
  return openActionDb().then(
    (db) =>
      new Promise<T>((resolve, reject) => {
        const store = db.transaction(ACTION_STORE, mode).objectStore(ACTION_STORE);
        const req = fn(store);
        req.onsuccess = () => resolve(req.result);
        req.onerror = () => reject(req.error);
      }),
  );
}

export async function enqueueAction(
  kind: QueuedActionKind,
  payload: Record<string, unknown>,
): Promise<QueuedAction> {
  const item: QueuedAction = {
    id: crypto.randomUUID(),
    kind,
    payload,
    queuedAt: new Date().toISOString(),
  };
  await actionTx('readwrite', (s) => s.add(item));
  return item;
}

export async function listQueuedActions(): Promise<QueuedAction[]> {
  return (await actionTx<QueuedAction[]>('readonly', (s) => s.getAll())) ?? [];
}

export async function removeQueuedAction(id: string): Promise<void> {
  await actionTx('readwrite', (s) => s.delete(id) as unknown as IDBRequest<undefined>);
}

export async function actionQueueDepth(): Promise<number> {
  return (await listQueuedActions()).length;
}

/**
 * Replay every queued action. `send` is injected (same reasoning as
 * replayQueue above) so this module never imports api.ts directly.
 * A 4xx also drops the item — retrying forever would jam the queue behind
 * one row the server has already rejected for good reason.
 */
export async function replayActionQueue(
  send: (action: QueuedAction) => Promise<void>,
): Promise<{ succeeded: number; failed: number }> {
  const items = await listQueuedActions();
  let succeeded = 0;
  let failed = 0;

  for (const item of items) {
    try {
      await send(item);
      await removeQueuedAction(item.id);
      succeeded += 1;
    } catch (err) {
      const status = (err as { status?: number }).status;
      if (status && status >= 400 && status < 500) {
        await removeQueuedAction(item.id);
      }
      failed += 1;
    }
  }

  return { succeeded, failed };
}