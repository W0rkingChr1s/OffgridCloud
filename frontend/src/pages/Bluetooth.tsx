import { useEffect, useRef, useState } from "react";
import {
  ApiError,
  createBlePairing,
  getBleStatus,
  listBleDevices,
  renameBleDevice,
  revokeBleDevice,
  setBleEnabled,
  type BleDevice,
  type BlePairing,
  type BleStatus,
} from "../api";
import { useAuth } from "../auth";
import Layout from "../components/Layout";
import QrCode from "../components/QrCode";
import { useToast } from "../toast";

// While a code is on screen, poll the device list so the page notices the
// phone finishing the pairing and can take the code down.
const PAIR_POLL_MS = 3000;

type ActivePairing = BlePairing & { deadline: number; known: Set<number> };

function date(iso: string): string {
  return new Date(iso + "Z").toLocaleDateString();
}

// The pairing link only helps on the phone that has the app installed.
const onPhone = typeof window !== "undefined" && window.matchMedia("(pointer: coarse)").matches;

function countdown(ms: number): string {
  const s = Math.max(0, Math.ceil(ms / 1000));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

export default function Bluetooth() {
  const toast = useToast();
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";
  const [status, setStatus] = useState<BleStatus | null>(null);
  const [devices, setDevices] = useState<BleDevice[]>([]);
  const [pairing, setPairing] = useState<ActivePairing | null>(null);
  const [now, setNow] = useState(() => Date.now());
  const [busy, setBusy] = useState(false);
  const devicesRef = useRef<BleDevice[]>([]);

  function loadDevices(): Promise<BleDevice[]> {
    return listBleDevices()
      .then((list) => {
        devicesRef.current = list;
        setDevices(list);
        return list;
      })
      .catch(() => devicesRef.current);
  }

  useEffect(() => {
    getBleStatus().then(setStatus).catch(() => setStatus(null));
    loadDevices();
  }, []);

  const expired = pairing !== null && now >= pairing.deadline;

  useEffect(() => {
    if (!pairing || expired) return;
    const tick = window.setInterval(() => setNow(Date.now()), 1000);
    const poll = window.setInterval(() => {
      loadDevices().then((list) => {
        const added = list.find((d) => !pairing.known.has(d.id));
        if (added) {
          setPairing(null);
          toast.success("Bluetooth", `„${added.name}“ ist gekoppelt.`);
        }
      });
    }, PAIR_POLL_MS);
    return () => {
      window.clearInterval(tick);
      window.clearInterval(poll);
    };
  }, [pairing, expired]);

  async function pair() {
    setBusy(true);
    try {
      const p = await createBlePairing();
      const known = new Set(devicesRef.current.map((d) => d.id));
      const start = Date.now();
      setNow(start);
      setPairing({ ...p, deadline: start + p.expires_in * 1000, known });
    } catch (err) {
      toast.error("Bluetooth", err instanceof ApiError ? err.message : "Code konnte nicht erzeugt werden");
    } finally {
      setBusy(false);
    }
  }

  async function toggle(enabled: boolean) {
    try {
      setStatus(await setBleEnabled(enabled));
      if (!enabled) setPairing(null);
    } catch {
      toast.error("Bluetooth", "Einstellung nicht gespeichert");
    }
  }

  async function rename(d: BleDevice) {
    const name = window.prompt("Neuer Name", d.name);
    if (!name?.trim()) return;
    await renameBleDevice(d.id, name.trim())
      .then(() => loadDevices())
      .catch(() => toast.error("Bluetooth", "Fehlgeschlagen"));
  }

  async function revoke(d: BleDevice) {
    if (!window.confirm(`„${d.name}“ entfernen? Das Gerät muss danach neu gekoppelt werden.`)) return;
    await revokeBleDevice(d.id)
      .then(() => loadDevices())
      .catch(() => toast.error("Bluetooth", "Fehlgeschlagen"));
  }

  const enabled = status?.enabled ?? false;

  return (
    <Layout>
      <h2 className="mb-1 text-lg font-semibold">Bluetooth</h2>
      <p className="mb-6 text-sm text-slate-400">
        Mit der OffgridCloud-App verwaltest du die Box per Bluetooth, auch wenn kein WLAN da ist.
        Jedes Handy wird einmal hier gekoppelt.
      </p>

      {status && (
        <div className="mb-6 space-y-3 rounded-2xl bg-slate-800/60 p-5 text-sm ring-1 ring-white/10">
          {isAdmin ? (
            <label className="flex items-start gap-3">
              <input
                type="checkbox"
                className="mt-1"
                checked={status.enabled}
                onChange={(e) => toggle(e.target.checked)}
              />
              <span className="text-slate-300">
                Bluetooth aktiv
                <span className="mt-0.5 block text-xs text-slate-500">
                  Aus: Die Box ist per Bluetooth nicht mehr sichtbar, gekoppelte Geräte bleiben gespeichert.
                </span>
              </span>
            </label>
          ) : (
            !status.enabled && <p className="text-slate-300">Bluetooth ist vom Administrator abgeschaltet.</p>
          )}
          {status.enabled && !status.service_running && (
            <div className="space-y-1 rounded-lg bg-amber-500/15 px-3 py-2 text-xs text-amber-200">
              <p>
                {!status.service_installed
                  ? "Der Bluetooth-Dienst ist auf der Box nicht installiert."
                  : !status.adapter_present
                    ? "Die Box findet keinen Bluetooth-Adapter (abgeschaltet, blockiert oder nicht vorhanden)."
                    : "Der Bluetooth-Dienst der Box meldet sich nicht."}{" "}
                Solange er nicht läuft, kann kein Handy koppeln oder sich verbinden.
              </p>
              {isAdmin && status.service_error && (
                <p>
                  Letzter Fehler: <span className="font-mono">{status.service_error}</span>
                </p>
              )}
              {isAdmin && (
                <p>
                  {!status.service_installed || status.service_error?.includes("dbus_fast") ? (
                    <>
                      Nachinstallieren:{" "}
                      <span className="break-all font-mono">sudo /opt/offgridcloud/src/deploy/ble/install.sh</span>.{" "}
                    </>
                  ) : (
                    <>
                      Diagnose: <span className="whitespace-nowrap font-mono">journalctl -u offgridcloud-ble -e</span>.{" "}
                    </>
                  )}
                  Details: docs/BETRIEB.md, Abschnitt 9d.
                </p>
              )}
            </div>
          )}
          {status.enabled && status.service_running && (
            <p className="text-xs text-slate-500">
              Sichtbar als <span className="font-mono text-slate-300">OGC-{status.box_name}</span>
            </p>
          )}
        </div>
      )}

      <section className="mb-8">
        {pairing && !expired ? (
          <div className="flex flex-col items-center gap-4 rounded-2xl bg-slate-800/60 p-5 ring-1 ring-ogc-teal/30 sm:flex-row sm:items-start">
            <QrCode value={pairing.link} label="QR-Code zum Koppeln" />
            <div className="min-w-0 flex-1 text-sm">
              <div className="mb-2 font-medium text-slate-200">Handy koppeln</div>
              <ol className="mb-3 list-decimal space-y-1 pl-5 text-slate-400">
                <li>In der OffgridCloud-App „Box koppeln“ wählen.</li>
                <li>Den QR-Code scannen oder den Code eintippen.</li>
                <li>In Bluetooth-Reichweite der Box bleiben, bis die App fertig ist.</li>
              </ol>
              <div className="mb-1 break-all font-mono text-base tracking-wider text-slate-100">{pairing.code}</div>
              <div className="mb-4 text-xs text-slate-500">
                Gilt noch {countdown(pairing.deadline - now)} min, nur einmal. Nicht weitergeben.
              </div>
              <div className="flex flex-wrap gap-3">
                {onPhone && (
                  <a
                    href={pairing.link}
                    className="rounded-lg border border-white/10 px-3 py-1.5 text-xs text-slate-200 hover:bg-white/5"
                  >
                    Auf diesem Handy in der App öffnen
                  </a>
                )}
                <button
                  type="button"
                  onClick={() => setPairing(null)}
                  className="text-xs text-slate-400 hover:text-slate-200"
                >
                  Abbrechen
                </button>
              </div>
            </div>
          </div>
        ) : (
          <div className="flex flex-wrap items-center gap-3">
            <button
              type="button"
              onClick={pair}
              disabled={busy || !enabled}
              className="rounded-lg bg-linear-to-r from-ogc-teal to-ogc-blue px-4 py-2 text-sm font-semibold text-white disabled:opacity-50"
            >
              {busy ? "…" : expired ? "Neuen Code erzeugen" : "Gerät koppeln"}
            </button>
            {expired && <span className="text-xs text-slate-500">Der Code ist abgelaufen.</span>}
          </div>
        )}
      </section>

      <h3 className="mb-3 text-sm font-semibold text-slate-300">
        {isAdmin ? "Gekoppelte Geräte (alle Benutzer)" : "Deine gekoppelten Geräte"}
      </h3>
      {devices.length === 0 ? (
        <p className="text-sm text-slate-500">Noch keine Geräte gekoppelt.</p>
      ) : (
        <ul className="space-y-2">
          {devices.map((d) => (
            <li
              key={d.id}
              className="flex items-center justify-between gap-3 rounded-xl bg-slate-800/60 px-4 py-3 ring-1 ring-white/10"
            >
              <div className="min-w-0">
                <div className="truncate text-sm font-medium text-slate-200">{d.name}</div>
                <div className="text-xs text-slate-500">
                  {isAdmin && d.user_id !== user?.id && `${d.user_email} · `}
                  gekoppelt {date(d.created_at)}
                  {d.last_seen_at ? ` · zuletzt verbunden ${date(d.last_seen_at)}` : " · noch nie verbunden"}
                </div>
              </div>
              <div className="flex shrink-0 gap-2">
                <button type="button" onClick={() => rename(d)} className="text-xs text-slate-400 hover:text-slate-200">
                  Umbenennen
                </button>
                <button type="button" onClick={() => revoke(d)} className="text-xs text-red-400 hover:text-red-300">
                  Entfernen
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}
    </Layout>
  );
}
