import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import {
  api,
  ApiError,
  type BandwidthStatus,
  type KnownNetwork,
  type NetworkApplyResult,
  type NetworkOverview,
  type NetworkSettings,
  type NetworkStatus,
  type SystemNetwork,
} from "../api";
import Collapsible from "../components/Collapsible";
import InfoTip from "../components/InfoTip";
import Layout from "../components/Layout";
import UplinkDiagram from "../components/UplinkDiagram";
import MoreMenu from "../components/MoreMenu";
import { SortTh, type SortOption, useSort } from "../components/Sort";

const NETWORK_SORT: SortOption<KnownNetwork>[] = [
  { key: "ssid", label: "SSID", get: (n) => n.ssid },
  { key: "priority", label: "Priorität", get: (n) => n.priority },
  { key: "password", label: "Passwort", get: (n) => (n.has_password ? 0 : 1) },
  { key: "auto", label: "Auto", get: (n) => (n.autoconnect ? 0 : 1) },
];

const MODE_LABELS: Record<NetworkStatus["mode"], string> = {
  ethernet: "Kabel (Ethernet)",
  client: "WLAN-Client",
  tether: "USB-Tethering",
  ap: "Eigener Access Point",
  offline: "Offline",
  unknown: "Unbekannt",
};

export default function Network() {
  const [data, setData] = useState<NetworkOverview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [applyMsg, setApplyMsg] = useState<NetworkApplyResult | null>(null);
  const [busy, setBusy] = useState(false);
  // Only for the diagram (which links net-merge bundles) and the metered rule.
  const [bandwidth, setBandwidth] = useState<BandwidthStatus | null>(null);

  function report(e: unknown) {
    setError(e instanceof ApiError ? e.message : "Fehler");
  }
  function load() {
    api<NetworkOverview>("/api/network").then(setData).catch(report);
    api<BandwidthStatus>("/api/bandwidth").then(setBandwidth).catch(() => setBandwidth(null));
  }
  useEffect(load, []);

  async function apply() {
    setBusy(true);
    setApplyMsg(null);
    setError(null);
    try {
      const r = await api<NetworkApplyResult>("/api/network/apply", { method: "POST" });
      setApplyMsg(r);
      load();
    } catch (e) {
      report(e);
    } finally {
      setBusy(false);
    }
  }

  const status = data?.status;

  return (
    <Layout>
      <div className="mb-6 flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="mb-1 text-2xl font-bold">Netzwerk-Redundanz</h2>
          <p className="text-sm text-slate-400">
            Rückfallebene: Fällt der Router aus, hostet die Box ihr eigenes WLAN — bis ein
            hinterlegtes Netzwerk wieder erreichbar ist.
          </p>
        </div>
        <button
          type="button"
          onClick={apply}
          disabled={busy}
          className="rounded-lg bg-linear-to-r from-ogc-teal to-ogc-blue px-4 py-2 text-sm font-semibold text-white disabled:opacity-50"
        >
          {busy ? "…" : "Anwenden"}
        </button>
      </div>

      {error && (
        <div className="mb-4 rounded-lg bg-red-500/15 px-3 py-2 text-sm text-red-300">{error}</div>
      )}
      {applyMsg && (
        <div
          className={`mb-4 rounded-lg px-3 py-2 text-sm ${
            applyMsg.ok ? "bg-emerald-500/15 text-emerald-300" : "bg-amber-500/15 text-amber-200"
          }`}
        >
          {applyMsg.message}
          {applyMsg.output && (
            <pre className="mt-2 max-h-40 overflow-auto whitespace-pre-wrap rounded-sm bg-black/30 p-2 text-xs text-slate-300">
              {applyMsg.output}
            </pre>
          )}
        </div>
      )}

      {status && <StatusCard status={status} bandwidth={bandwidth} />}

      {status && !status.supported && (
        <div className="mb-6 rounded-2xl bg-slate-800/60 p-5 text-sm text-slate-300 ring-1 ring-amber-400/20">
          <div className="mb-1 font-medium text-amber-200">Live-Steuerung nicht verfügbar</div>
          {status.detail || "NetworkManager (nmcli) wurde auf diesem Host nicht gefunden."}
          <div className="mt-2 text-xs text-slate-500">
            Die Einstellungen unten werden trotzdem gespeichert und beim nächsten „Anwenden“ auf
            einem Gerät mit NetworkManager (z. B. Raspberry Pi OS) übernommen. Details:
            docs/NETZWERK-REDUNDANZ.md.
          </div>
        </div>
      )}

      {data && (
        <>
          <FallbackCard settings={data.settings} onSaved={load} onError={report} />
          <KnownNetworks
            networks={data.known_networks}
            systemNetworks={data.system_networks ?? []}
            onChanged={load}
            onError={report}
          />
        </>
      )}
    </Layout>
  );
}

function StatusCard({ status, bandwidth }: { status: NetworkStatus; bandwidth: BandwidthStatus | null }) {
  const online = status.online;
  const badge = status.ap_active
    ? { text: "AP aktiv", cls: "bg-amber-500/20 text-amber-200" }
    : online
      ? { text: "Online", cls: "bg-emerald-500/20 text-emerald-300" }
      : { text: "Offline", cls: "bg-red-500/20 text-red-300" };
  const links = status.links ?? [];
  return (
    <div className="mb-6 rounded-2xl bg-slate-800/60 p-5 ring-1 ring-white/10">
      <div className="mb-3 flex items-center gap-3">
        <span className="text-sm font-medium text-slate-400">Aktueller Status</span>
        <span className={`rounded-full px-2 py-0.5 text-xs font-semibold ${badge.cls}`}>
          {badge.text}
        </span>
      </div>
      {status.tether && <TetherBanner status={status} bandwidth={bandwidth} />}
      <div className="grid gap-4 sm:grid-cols-3">
        <Field
          label="Modus"
          value={MODE_LABELS[status.mode] + (status.metered ? " · getaktet" : "")}
        />
        <Field label="Verbunden mit" value={connectedTo(status)} />
        <Field label="IP-Adresse" value={status.uplink_ip || status.wifi_ip || "—"} />
      </div>
      {links.length > 1 && (
        <div className="mt-5 border-t border-white/10 pt-4">
          <div className="mb-2 text-sm font-medium text-slate-400">Leitungen</div>
          <UplinkDiagram
            links={links}
            bonding={
              bandwidth?.bonding_available
                ? {
                    enabled: bandwidth.bonding_enabled,
                    links: bandwidth.bonding_links,
                    bypass: bandwidth.bonding_bypass ?? "",
                  }
                : undefined
            }
          />
        </div>
      )}
    </div>
  );
}

const METERED_RULE: Record<BandwidthStatus["metered_mode"], string> = {
  allow: "Uploads laufen normal weiter",
  limit: "Uploads laufen bis zum Tagesvolumen",
  pause: "Uploads sind pausiert, bis Kabel oder WLAN da ist",
};

/** Hard to miss: a phone is plugged in — in use (mobile data!) or in reserve. */
function TetherBanner({ status, bandwidth }: { status: NetworkStatus; bandwidth: BandwidthStatus | null }) {
  const phone = TETHER_NAMES[status.tether!];
  const inUse = status.mode === "tether";
  const bundled =
    !inUse &&
    !!bandwidth?.bonding_enabled &&
    !bandwidth.bonding_bypass &&
    bandwidth.bonding_links.some((b) => b.used && status.links?.some((l) => l.kind === "tether" && l.device === b.name));
  return (
    <div
      className={`mb-4 rounded-xl px-3 py-2 text-sm ring-1 ${
        inUse || bundled ? "bg-amber-500/15 text-amber-200 ring-amber-400/30" : "bg-white/5 text-slate-300 ring-white/10"
      }`}
    >
      <div className="font-semibold">
        {inUse
          ? `USB-Tethering aktiv: Die Box ist über das ${phone} online`
          : bundled
            ? `USB-Tethering aktiv: Das ${phone} wird mitgebündelt`
            : `${phone} per USB angeschlossen (Reserve)`}
      </div>
      <div className="mt-0.5 text-xs opacity-90">
        {inUse || bundled ? "Uploads verbrauchen mobiles Datenvolumen" : "Springt ein, wenn Kabel und WLAN wegfallen"}
        {bandwidth && (inUse || bundled) && ` · ${METERED_RULE[bandwidth.metered_mode]}`}
        {status.tether_ip && ` · ${status.tether_ip}`}
        {" · "}
        <Link to="/admin/bandwidth" className="underline">
          Regel ändern
        </Link>
      </div>
    </div>
  );
}

const TETHER_NAMES = { iphone: "iPhone", android: "Android-Handy" } as const;

function connectedTo(status: NetworkStatus): string {
  if (status.mode === "tether" && status.tether) return TETHER_NAMES[status.tether];
  if (status.mode === "client" && status.wifi_ssid) return status.wifi_ssid;
  if (status.mode === "ethernet") return "Kabel";
  return status.wifi_ssid || status.ap_ssid || "—";
}

function Field({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="text-xs text-slate-500">{label}</div>
      <div className="truncate text-lg font-semibold text-white">{value}</div>
    </div>
  );
}

function FallbackCard({
  settings,
  onSaved,
  onError,
}: {
  settings: NetworkSettings;
  onSaved: () => void;
  onError: (e: unknown) => void;
}) {
  async function toggle(enabled: boolean) {
    try {
      await api<NetworkSettings>("/api/network/settings", {
        method: "PUT",
        body: JSON.stringify({ fallback_enabled: enabled }),
      });
      onSaved();
    } catch (e) {
      onError(e);
    }
  }

  return (
    <div className="mb-6 rounded-2xl bg-slate-800/60 p-5 ring-1 ring-white/10">
      <label className="flex items-start gap-3">
        <input
          type="checkbox"
          className="mt-1"
          checked={settings.fallback_enabled}
          onChange={(e) => toggle(e.target.checked)}
        />
        <span className="text-sm">
          <span className="inline-flex items-center gap-1.5 font-medium text-white">
            Rückfall-WLAN
            <InfoTip text="Ist kein hinterlegtes Netzwerk erreichbar, öffnet die Box automatisch ihren eigenen Access Point, damit das Feld-Team weiter hochladen kann. Sobald ein hinterlegtes Netzwerk wieder erreichbar ist, verlässt sie den AP." />
          </span>
          <span className="mt-0.5 block text-xs text-slate-500">
            {settings.fallback_enabled
              ? "Eigenes WLAN, wenn kein bekanntes Netz erreichbar ist."
              : "Aus – ohne bekanntes Netz ist die Box nicht erreichbar."}
          </span>
        </span>
      </label>
      <div className="mt-4">
        <Collapsible
          nested
          title="AP-Einstellungen"
          summary={`${settings.ap_ssid} · ${settings.ap_has_password ? "mit Passwort" : "offen"} · ${settings.ap_address}`}
        >
          <FallbackSettings settings={settings} onSaved={onSaved} onError={onError} />
        </Collapsible>
      </div>
    </div>
  );
}

function FallbackSettings({
  settings,
  onSaved,
  onError,
}: {
  settings: NetworkSettings;
  onSaved: () => void;
  onError: (e: unknown) => void;
}) {
  const [form, setForm] = useState(settings);
  const [password, setPassword] = useState("");
  const [saving, setSaving] = useState(false);
  useEffect(() => setForm(settings), [settings]);

  async function save(patch: Record<string, unknown>) {
    setSaving(true);
    try {
      await api<NetworkSettings>("/api/network/settings", {
        method: "PUT",
        body: JSON.stringify(patch),
      });
      onSaved();
    } catch (e) {
      onError(e);
    } finally {
      setSaving(false);
    }
  }

  return (
    <div>
      <div className="grid gap-4 sm:grid-cols-2">
        <label className="text-sm">
          <span className="mb-1 block text-slate-400">AP-Name (SSID)</span>
          <input
            className="w-full rounded-lg border border-white/10 bg-slate-900/60 px-3 py-2 outline-hidden focus:border-ogc-teal"
            value={form.ap_ssid}
            onChange={(e) => setForm({ ...form, ap_ssid: e.target.value })}
            onBlur={() => form.ap_ssid !== settings.ap_ssid && save({ ap_ssid: form.ap_ssid })}
          />
        </label>
        <label className="text-sm">
          <span className="mb-1 block text-slate-400">AP-Passwort</span>
          <input
            type="password"
            className="w-full rounded-lg border border-white/10 bg-slate-900/60 px-3 py-2 outline-hidden focus:border-ogc-teal"
            placeholder={settings.ap_has_password ? "•••••••• (gesetzt)" : "8–63 Zeichen (leer = offen)"}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            onBlur={() => {
              if (password) {
                save({ ap_password: password });
                setPassword("");
              }
            }}
          />
        </label>
        <label className="text-sm">
          <span className="mb-1 block text-slate-400">AP-Adresse</span>
          <input
            className="w-full rounded-lg border border-white/10 bg-slate-900/60 px-3 py-2 outline-hidden focus:border-ogc-teal"
            value={form.ap_address}
            onChange={(e) => setForm({ ...form, ap_address: e.target.value })}
            onBlur={() =>
              form.ap_address !== settings.ap_address && save({ ap_address: form.ap_address })
            }
          />
        </label>
        <label className="text-sm">
          <span className="mb-1 block text-slate-400">Ländercode (WLAN-Regulierung)</span>
          <input
            className="w-full rounded-lg border border-white/10 bg-slate-900/60 px-3 py-2 outline-hidden focus:border-ogc-teal"
            placeholder="z. B. DE"
            maxLength={2}
            value={form.country_code}
            onChange={(e) => setForm({ ...form, country_code: e.target.value.toUpperCase() })}
            onBlur={() =>
              form.country_code !== settings.country_code &&
              save({ country_code: form.country_code })
            }
          />
        </label>
        <label className="text-sm">
          <span className="mb-1 block text-slate-400">Prüf-Intervall (Sekunden)</span>
          <input
            type="number"
            min={5}
            max={3600}
            className="w-full rounded-lg border border-white/10 bg-slate-900/60 px-3 py-2 outline-hidden focus:border-ogc-teal"
            value={form.check_interval}
            onChange={(e) => setForm({ ...form, check_interval: Number(e.target.value) })}
            onBlur={() =>
              form.check_interval !== settings.check_interval &&
              save({ check_interval: form.check_interval })
            }
          />
        </label>
        <label className="text-sm">
          <span className="mb-1 block text-slate-400">Fehlversuche bis Umschalten</span>
          <input
            type="number"
            min={1}
            max={20}
            className="w-full rounded-lg border border-white/10 bg-slate-900/60 px-3 py-2 outline-hidden focus:border-ogc-teal"
            value={form.fail_threshold}
            onChange={(e) => setForm({ ...form, fail_threshold: Number(e.target.value) })}
            onBlur={() =>
              form.fail_threshold !== settings.fail_threshold &&
              save({ fail_threshold: form.fail_threshold })
            }
          />
        </label>
        <label className="text-sm">
          <span className="mb-1 block text-slate-400">Suche im AP-Modus (Sekunden)</span>
          <input
            type="number"
            min={30}
            max={3600}
            className="w-full rounded-lg border border-white/10 bg-slate-900/60 px-3 py-2 outline-hidden focus:border-ogc-teal"
            value={form.ap_probe_interval}
            onChange={(e) => setForm({ ...form, ap_probe_interval: Number(e.target.value) })}
            onBlur={() =>
              form.ap_probe_interval !== settings.ap_probe_interval &&
              save({ ap_probe_interval: form.ap_probe_interval })
            }
          />
          <span className="mt-1 block text-xs text-slate-500">
            Scan nach bekannten WLANs, während der AP läuft — ohne Unterbrechung.
          </span>
        </label>
        <label className="text-sm">
          <span className="mb-1 block text-slate-400">AP kurz abschalten zum Suchen (Sekunden)</span>
          <input
            type="number"
            min={60}
            max={86400}
            className="w-full rounded-lg border border-white/10 bg-slate-900/60 px-3 py-2 outline-hidden focus:border-ogc-teal"
            value={form.ap_blind_probe_interval}
            onChange={(e) =>
              setForm({ ...form, ap_blind_probe_interval: Number(e.target.value) })
            }
            onBlur={() =>
              form.ap_blind_probe_interval !== settings.ap_blind_probe_interval &&
              save({ ap_blind_probe_interval: form.ap_blind_probe_interval })
            }
          />
          <span className="mt-1 block text-xs text-slate-500">
            Nötig, wenn der WLAN-Chip im AP-Modus nicht scannen kann (Raspberry Pi). Verbundene
            Geräte verlieren dabei für einige Sekunden die Verbindung.
          </span>
        </label>
      </div>

      <label className="mt-4 flex items-center gap-2 text-sm text-slate-300">
        <input
          type="checkbox"
          checked={form.ap_hidden}
          onChange={(e) => save({ ap_hidden: e.target.checked })}
        />
        AP-Namen verbergen (SSID nicht senden)
      </label>
      {saving && <div className="mt-2 text-xs text-slate-500">Speichern…</div>}
    </div>
  );
}

function KnownNetworks({
  networks,
  systemNetworks,
  onChanged,
  onError,
}: {
  networks: KnownNetwork[];
  systemNetworks: SystemNetwork[];
  onChanged: () => void;
  onError: (e: unknown) => void;
}) {
  const [ssid, setSsid] = useState("");
  const [password, setPassword] = useState("");
  const [priority, setPriority] = useState(0);
  const [adding, setAdding] = useState(false);
  const sort = useSort(networks, NETWORK_SORT, { key: "priority", dir: "desc" });

  async function add() {
    if (!ssid.trim()) return;
    try {
      await api<KnownNetwork>("/api/network/known", {
        method: "POST",
        body: JSON.stringify({ ssid: ssid.trim(), password, priority }),
      });
      setSsid("");
      setPassword("");
      setPriority(0);
      setAdding(false);
      onChanged();
    } catch (e) {
      onError(e);
    }
  }

  async function remove(id: number) {
    try {
      await api(`/api/network/known/${id}`, { method: "DELETE" });
      onChanged();
    } catch (e) {
      onError(e);
    }
  }

  return (
    <div className="mb-6 rounded-2xl bg-slate-800/60 p-5 ring-1 ring-white/10">
      <div className="mb-3 flex items-center justify-between gap-3">
        <div className="flex items-center gap-1.5 text-sm font-medium text-white">
          Hinterlegte Netzwerke
          <InfoTip text="WLANs, mit denen sich die Box automatisch verbindet. Höhere Priorität wird bevorzugt. Ist eines erreichbar, verlässt die Box den Rückfall-AP. „System“-Netzwerke stammen aus der Betriebssystem-Einrichtung (z. B. Raspberry Pi Imager); sie lassen sich hier nicht bearbeiten, nur per nmtui/nmcli auf der Box." />
        </div>
        {!adding && (
          <button
            type="button"
            onClick={() => setAdding(true)}
            className="whitespace-nowrap rounded-lg border border-white/10 px-3 py-1.5 text-xs font-medium text-slate-200 hover:bg-white/5"
          >
            + Netzwerk hinzufügen
          </button>
        )}
      </div>

      {adding && (
        <div className="mb-4 grid gap-2 sm:grid-cols-[1fr_1fr_auto_auto_auto]">
          <input
            className="rounded-lg border border-white/10 bg-slate-900/60 px-3 py-2 text-sm outline-hidden focus:border-ogc-teal"
            placeholder="SSID"
            autoFocus
            value={ssid}
            onChange={(e) => setSsid(e.target.value)}
          />
          <input
            type="password"
            className="rounded-lg border border-white/10 bg-slate-900/60 px-3 py-2 text-sm outline-hidden focus:border-ogc-teal"
            placeholder="Passwort (leer = offen)"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
          <input
            type="number"
            min={0}
            className="w-24 rounded-lg border border-white/10 bg-slate-900/60 px-3 py-2 text-sm outline-hidden focus:border-ogc-teal"
            placeholder="Prio"
            value={priority}
            onChange={(e) => setPriority(Number(e.target.value))}
          />
          <button
            type="button"
            onClick={add}
            className="rounded-lg border border-white/10 px-4 py-2 text-sm font-medium text-slate-200 hover:bg-white/5"
          >
            Hinzufügen
          </button>
          <button
            type="button"
            onClick={() => setAdding(false)}
            className="rounded-lg px-3 py-2 text-sm text-slate-400 hover:text-white"
          >
            Abbrechen
          </button>
        </div>
      )}

      {networks.length === 0 && systemNetworks.length === 0 ? (
        <p className="text-sm text-slate-500">Noch keine Netzwerke hinterlegt.</p>
      ) : (
        <div className="overflow-x-auto rounded-xl ring-1 ring-white/10">
          <table className="w-full text-left text-sm">
            <thead className="bg-slate-800/80 text-slate-400">
              <tr>
                <SortTh sort={sort} field="ssid" className="py-2!">SSID</SortTh>
                <SortTh sort={sort} field="priority" className="py-2!">Priorität</SortTh>
                <SortTh sort={sort} field="password" className="hidden py-2! sm:table-cell">Passwort</SortTh>
                <SortTh sort={sort} field="auto" className="hidden py-2! sm:table-cell">Auto</SortTh>
                <th className="px-4 py-2" aria-label="Aktionen" />
              </tr>
            </thead>
            <tbody>
              {sort.sorted.map((n) => (
                <tr key={n.id} className="border-t border-white/5 bg-slate-900/40">
                  <td className="px-4 py-2 font-medium text-white">{n.ssid}</td>
                  <td className="px-4 py-2 text-slate-400">{n.priority}</td>
                  <td className="hidden px-4 py-2 text-slate-400 sm:table-cell">{n.has_password ? "🔒" : "offen"}</td>
                  <td className="hidden px-4 py-2 text-slate-400 sm:table-cell">{n.autoconnect ? "ja" : "nein"}</td>
                  <td className="px-4 py-2">
                    <div className="flex justify-end">
                      <MoreMenu
                        items={[{ label: `„${n.ssid}“ entfernen`, danger: true, onClick: () => remove(n.id) }]}
                      />
                    </div>
                  </td>
                </tr>
              ))}
              {systemNetworks.map((n) => (
                <tr key={`sys-${n.name}`} className="border-t border-white/5 bg-slate-900/20">
                  <td className="px-4 py-2 font-medium text-white">
                    {n.ssid}
                    <span
                      className="ml-2 rounded-full bg-sky-500/15 px-2 py-0.5 text-xs font-normal text-sky-300"
                      title={`NetworkManager-Profil „${n.name}“ — außerhalb von OffgridCloud angelegt (z. B. Raspberry Pi Imager)`}
                    >
                      System
                    </span>
                    {n.active && (
                      <span className="ml-1 rounded-full bg-emerald-500/15 px-2 py-0.5 text-xs font-normal text-emerald-300">
                        verbunden
                      </span>
                    )}
                  </td>
                  <td className="px-4 py-2 text-slate-400">{n.priority}</td>
                  <td className="hidden px-4 py-2 text-slate-400 sm:table-cell">{n.has_password ? "🔒" : "offen"}</td>
                  <td className="hidden px-4 py-2 text-slate-400 sm:table-cell">{n.autoconnect ? "ja" : "nein"}</td>
                  <td className="px-4 py-2" />
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
