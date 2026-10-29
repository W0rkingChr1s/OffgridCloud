import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type FormEvent } from "react";
import {
  api,
  ApiError,
  type HealthCheck,
  type HealthHistory,
  type HealthPoint,
  type HealthSnapshot,
  type HealthStatus,
  type MonitorToken,
  type MonitorTokenCreated,
} from "../api";
import { copyText } from "../clipboard";
import Layout from "../components/Layout";

const LIVE_EVERY_MS = 5000;
const HISTORY_EVERY_MS = 60000;

// Status colours: marks (rings, dots) use the 500 steps, text the 300/400 steps,
// which the light theme remaps to darker shades (index.css). Status is never
// colour alone — every use pairs it with an icon and a word.
const STATUS = {
  ok: { label: "OK", word: "Alles in Ordnung", text: "text-emerald-300", mark: "text-emerald-500", bg: "bg-emerald-500" },
  warning: { label: "Warnung", word: "Warnungen", text: "text-amber-300", mark: "text-amber-500", bg: "bg-amber-500" },
  error: { label: "Störung", word: "Störung", text: "text-red-300", mark: "text-red-500", bg: "bg-red-500" },
} satisfies Record<HealthStatus, { label: string; word: string; text: string; mark: string; bg: string }>;

function rate(value: number | null | undefined, warn: number, err: number): HealthStatus {
  if (value == null) return "ok";
  return value >= err ? "error" : value >= warn ? "warning" : "ok";
}

function bytes(n: number): string {
  const units = ["B", "KB", "MB", "GB", "TB"];
  let v = n;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i++;
  }
  return `${v >= 10 || i === 0 ? v.toFixed(0) : v.toFixed(1)} ${units[i]}`;
}

function duration(secs: number | null | undefined): string {
  if (secs == null) return "—";
  const s = Math.round(secs);
  if (s < 60) return `${s} s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m} min`;
  const h = Math.floor(m / 60);
  if (h < 48) return m % 60 ? `${h} h ${m % 60} min` : `${h} h`;
  return `${Math.floor(h / 24)} Tage`;
}

function clock(t: number): string {
  return new Date(t * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

// --- Icons ------------------------------------------------------------------

function StatusIcon({ status, className = "h-4 w-4" }: { status: HealthStatus; className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" className={className} aria-hidden>
      {status === "ok" && <path d="M5 12.5l4.5 4.5L19 7.5" />}
      {status === "warning" && <path d="M12 7v6M12 17.5v.01" />}
      {status === "error" && <path d="M7 7l10 10M17 7L7 17" />}
    </svg>
  );
}

// --- Data hooks ---------------------------------------------------------------

function usePolling<T>(path: string, every: number) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState("");
  const load = useCallback(async () => {
    try {
      setData(await api<T>(path));
      setError("");
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Box nicht erreichbar");
    }
  }, [path]);
  useEffect(() => {
    load();
    const id = window.setInterval(() => {
      if (!document.hidden) load();
    }, every);
    return () => window.clearInterval(id);
  }, [load, every]);
  return { data, error, reload: load };
}

function useWidth<T extends HTMLElement>() {
  const ref = useRef<T>(null);
  const [width, setWidth] = useState(0);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const ro = new ResizeObserver(([entry]) => setWidth(entry.contentRect.width));
    ro.observe(el);
    setWidth(el.getBoundingClientRect().width);
    return () => ro.disconnect();
  }, []);
  return [ref, width] as const;
}

// --- Hero ---------------------------------------------------------------------

function Hero({ snap }: { snap: HealthSnapshot }) {
  const s = STATUS[snap.status];
  const issues = snap.checks.filter((c) => c.status !== "ok");
  return (
    <div className="ogc-rise relative mb-6 overflow-hidden rounded-2xl bg-slate-800/60 p-5 shadow-lg ring-1 ring-white/5 sm:p-6">
      <div className={`pointer-events-none absolute -top-24 -right-24 h-64 w-64 rounded-full opacity-20 blur-3xl ${s.bg}`} />
      <div className="relative flex flex-wrap items-center gap-5">
        <div className="relative flex h-16 w-16 shrink-0 items-center justify-center">
          <span className={`absolute inset-0 rounded-full opacity-30 motion-safe:animate-ping ${s.bg}`} style={{ animationDuration: snap.status === "ok" ? "3s" : "1.4s" }} />
          <span className={`relative flex h-14 w-14 items-center justify-center rounded-full text-white shadow-lg ${s.bg}`}>
            <StatusIcon status={snap.status} className="h-7 w-7" />
          </span>
        </div>
        <div className="min-w-0 flex-1">
          <div className="text-sm font-medium text-slate-400">Gesamtzustand</div>
          <div className="text-3xl font-bold text-white sm:text-4xl">{s.word}</div>
          <div className="mt-1 text-sm text-slate-400">
            {issues.length
              ? issues.map((c) => `${c.label}: ${c.message}`).join(" · ")
              : `${snap.checks.length} Prüfungen ohne Befund`}
          </div>
        </div>
        <dl className="grid grid-cols-3 gap-x-6 gap-y-1 text-sm">
          <dt className="text-slate-500">Gerät</dt>
          <dt className="text-slate-500">Version</dt>
          <dt className="text-slate-500">Läuft seit</dt>
          <dd className="truncate font-medium text-white">{snap.hostname}</dd>
          <dd className="font-medium text-white">{snap.version}</dd>
          <dd className="font-medium text-white">{duration(snap.system.uptime_seconds)}</dd>
        </dl>
      </div>
    </div>
  );
}

// --- Ring gauge ---------------------------------------------------------------

function Gauge({
  label,
  value,
  max,
  display,
  sub,
  status,
  delay = 0,
}: {
  label: string;
  value: number | null;
  max: number;
  display: string;
  sub?: string;
  status: HealthStatus;
  delay?: number;
}) {
  const r = 42;
  const circ = 2 * Math.PI * r;
  // 270° arc, open at the bottom.
  const arc = circ * 0.75;
  const frac = value == null ? 0 : Math.max(0, Math.min(1, value / max));
  // Start empty, then fill — the transition does the animation on mount and update.
  const [shown, setShown] = useState(0);
  useEffect(() => {
    const id = window.setTimeout(() => setShown(frac), 60 + delay);
    return () => window.clearTimeout(id);
  }, [frac, delay]);
  const s = STATUS[status];
  return (
    <div className="ogc-rise flex flex-col items-center rounded-2xl bg-slate-800/60 p-4 shadow-lg ring-1 ring-white/5" style={{ animationDelay: `${delay}ms` }}>
      <div className="relative h-32 w-32">
        <svg viewBox="0 0 100 100" className="h-full w-full" role="img" aria-label={`${label}: ${display}`}>
          <g transform="rotate(135 50 50)">
            <circle cx="50" cy="50" r={r} fill="none" stroke="currentColor" strokeWidth="8" strokeLinecap="round" strokeDasharray={`${arc} ${circ}`} className="text-white/10" />
            <circle
              cx="50"
              cy="50"
              r={r}
              fill="none"
              stroke="currentColor"
              strokeWidth="8"
              strokeLinecap="round"
              strokeDasharray={`${arc} ${circ}`}
              strokeDashoffset={arc * (1 - shown)}
              className={`${status === "ok" ? "text-ogc-teal" : s.mark} transition-[stroke-dashoffset,color] duration-1000 ease-out motion-reduce:transition-none`}
            />
          </g>
        </svg>
        <div className="absolute inset-0 flex flex-col items-center justify-center">
          <span className="text-2xl font-bold text-white">{display}</span>
          {status !== "ok" && (
            <span className={`mt-0.5 flex items-center gap-1 text-xs font-semibold ${s.text}`}>
              <StatusIcon status={status} className="h-3 w-3" />
              {s.label}
            </span>
          )}
        </div>
      </div>
      <div className="mt-1 text-sm font-medium text-slate-300">{label}</div>
      {sub && <div className="text-xs text-slate-500">{sub}</div>}
    </div>
  );
}

// --- Checks -------------------------------------------------------------------

function CheckTile({ check, index }: { check: HealthCheck; index: number }) {
  const s = STATUS[check.status];
  return (
    <div
      className="ogc-rise flex items-start gap-3 rounded-xl bg-slate-800/60 p-3 ring-1 ring-white/5"
      style={{ animationDelay: `${120 + index * 40}ms` }}
    >
      <span className={`mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-white ${s.bg}`}>
        <StatusIcon status={check.status} className="h-3.5 w-3.5" />
      </span>
      <div className="min-w-0">
        <div className="flex items-baseline gap-2">
          <span className="font-medium text-white">{check.label}</span>
          <span className={`text-xs font-semibold ${s.text}`}>{s.label}</span>
        </div>
        <div className="truncate text-sm text-slate-400" title={check.message}>
          {check.message}
        </div>
      </div>
    </div>
  );
}

// --- Network ------------------------------------------------------------------

function SignalBars({ quality }: { quality: number }) {
  const bars = quality >= 75 ? 4 : quality >= 50 ? 3 : quality >= 25 ? 2 : quality > 0 ? 1 : 0;
  return (
    <span className="inline-flex h-4 items-end gap-0.5" aria-hidden>
      {[1, 2, 3, 4].map((i) => (
        <span
          key={i}
          className={`w-1 rounded-sm transition-colors duration-500 ${i <= bars ? "bg-ogc-teal" : "bg-white/15"}`}
          style={{ height: `${i * 25}%` }}
        />
      ))}
    </span>
  );
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-3 py-1.5 text-sm">
      <span className="text-slate-400">{label}</span>
      <span className="text-right font-medium text-white">{children}</span>
    </div>
  );
}

function NetworkCard({ snap }: { snap: HealthSnapshot }) {
  const n = snap.network;
  const online = n.online;
  const state: HealthStatus = online === false ? "error" : "ok";
  return (
    <section className="ogc-rise rounded-2xl bg-slate-800/60 p-5 shadow-lg ring-1 ring-white/5">
      <div className="mb-3 flex items-center gap-3">
        <span className="relative flex h-3 w-3">
          <span className={`absolute inline-flex h-full w-full rounded-full opacity-60 motion-safe:animate-ping ${STATUS[state].bg}`} />
          <span className={`relative inline-flex h-3 w-3 rounded-full ${STATUS[state].bg}`} />
        </span>
        <h3 className="text-lg font-semibold text-white">Internet</h3>
        <span className={`text-sm font-medium ${STATUS[state].text}`}>
          {online == null ? "wird geprüft …" : online ? "verbunden" : `getrennt seit ${duration(n.offline_for_seconds)}`}
        </span>
      </div>
      <div className="divide-y divide-white/5">
        <Row label="Latenz">{n.latency_ms != null ? `${n.latency_ms.toFixed(0)} ms` : "—"}</Row>
        <Row label="Ausfälle (24 h)">
          {n.outages_24h} {n.outages_24h > 0 && <span className="font-normal text-slate-500">· {duration(n.downtime_24h_seconds)} gesamt</span>}
        </Row>
        <Row label="Letzter Ausfall">
          {n.last_outage
            ? `${duration(n.last_outage.seconds)}, bis ${new Date(n.last_outage.ended_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`
            : "keiner"}
        </Row>
        <Row label="Bandbreite">{n.bandwidth_kbps ? `${(n.bandwidth_kbps / 1024).toFixed(1)} MB/s` : "—"}</Row>
        {n.wifi.map((w) => (
          <Row key={w.interface} label={`WLAN ${w.interface}`}>
            <span className="inline-flex items-center gap-2">
              <SignalBars quality={w.quality_percent} />
              {w.signal_dbm} dBm
            </span>
          </Row>
        ))}
        <Row label="VPN">{n.vpn_connected == null ? "—" : n.vpn_connected ? "verbunden" : "aus"}</Row>
        <Row label="Interne IP">{n.internal_ip ?? "—"}</Row>
        <Row label="Externe IP">{n.external_ip ?? "—"}</Row>
      </div>
    </section>
  );
}

const FLAG_LABEL: Record<string, string> = {
  under_voltage: "Unterspannung",
  throttled: "Gedrosselt",
  freq_capped: "Takt begrenzt",
  soft_temp_limit: "Temperaturgrenze",
};

function SystemCard({ snap }: { snap: HealthSnapshot }) {
  const sys = snap.system;
  const t = sys.throttling;
  const disk = snap.storage.buffer;
  return (
    <section className="ogc-rise rounded-2xl bg-slate-800/60 p-5 shadow-lg ring-1 ring-white/5">
      <h3 className="mb-3 text-lg font-semibold text-white">System</h3>
      <div className="divide-y divide-white/5">
        <Row label="Last (1/5/15 min)">{sys.load_average ? sys.load_average.map((v) => v.toFixed(2)).join(" / ") : "—"}</Row>
        <Row label="CPU-Kerne">{sys.cpu_count ?? "—"}</Row>
        <Row label="Arbeitsspeicher">
          {sys.memory ? `${bytes(sys.memory.used)} von ${bytes(sys.memory.total)}` : "—"}
        </Row>
        {sys.memory && sys.memory.swap_total > 0 && (
          <Row label="Swap">{`${bytes(sys.memory.swap_used)} von ${bytes(sys.memory.swap_total)}`}</Row>
        )}
        <Row label="Puffer-Speicher">{disk ? `${bytes(disk.free)} frei von ${bytes(disk.total)}` : "—"}</Row>
        <Row label="Warteschlange">
          {snap.transfers.queued ?? 0} wartend · {snap.transfers.running ?? 0} aktiv
          {(snap.transfers.failed ?? 0) > 0 && <span className="text-amber-300"> · {snap.transfers.failed} fehlgeschlagen</span>}
        </Row>
        <Row label="Cloud-Ziele">
          {snap.providers.total ? `${snap.providers.ok}/${snap.providers.total} verbunden` : "keine"}
        </Row>
        <Row label="rclone">{snap.rclone.available ? snap.rclone.version : "nicht verfügbar"}</Row>
      </div>
      {t && (
        <div className="mt-4">
          <div className="mb-2 text-sm font-medium text-slate-300">Stromversorgung (Raspberry Pi)</div>
          <div className="flex flex-wrap gap-2">
            {Object.keys(FLAG_LABEL).map((flag) => {
              const now = t.now[flag as keyof typeof t.now];
              const past = t.since_boot[flag as keyof typeof t.since_boot];
              const status: HealthStatus = now ? (flag === "under_voltage" ? "error" : "warning") : past ? "warning" : "ok";
              const s = STATUS[status];
              return (
                <span key={flag} className="inline-flex items-center gap-1.5 rounded-full bg-white/5 px-2.5 py-1 text-xs">
                  <span className={`flex h-4 w-4 items-center justify-center rounded-full text-white ${s.bg}`}>
                    <StatusIcon status={status} className="h-2.5 w-2.5" />
                  </span>
                  <span className="text-slate-300">{FLAG_LABEL[flag]}</span>
                  <span className={s.text}>{now ? "jetzt" : past ? "seit Start" : "nie"}</span>
                </span>
              );
            })}
          </div>
        </div>
      )}
    </section>
  );
}

// --- History charts -----------------------------------------------------------

const RANGES = [
  { label: "1 h", secs: 3600 },
  { label: "6 h", secs: 6 * 3600 },
  { label: "24 h", secs: 24 * 3600 },
];

type Field = "cpu_percent" | "memory_percent" | "temperature_c" | "latency_ms";

function niceMax(v: number): number {
  if (v <= 0) return 1;
  const pow = 10 ** Math.floor(Math.log10(v));
  const n = v / pow;
  return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 5 ? 5 : 10) * pow;
}

function TrendChart({
  title,
  points,
  field,
  unit,
  fixedMax,
  from,
  to,
  digits = 0,
}: {
  title: string;
  points: HealthPoint[];
  field: Field;
  unit: string;
  fixedMax?: number;
  from: number;
  to: number;
  digits?: number;
}) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<HealthPoint | null>(null);
  const height = 120;
  const pad = { top: 8, right: 8, bottom: 20, left: 36 };
  const w = Math.max(0, width - pad.left - pad.right);
  const h = height - pad.top - pad.bottom;

  const visible = points.filter((p) => p.t >= from);
  const values = visible.map((p) => p[field]).filter((v): v is number => v != null);
  const max = fixedMax ?? niceMax(Math.max(1, ...values) * 1.15);
  const x = (t: number) => pad.left + ((t - from) / (to - from)) * w;
  const y = (v: number) => pad.top + h - (Math.min(v, max) / max) * h;

  // Break the line at missing values (restart / probe failure) instead of bridging.
  const segments: HealthPoint[][] = [];
  let current: HealthPoint[] = [];
  for (const p of visible) {
    if (p[field] == null) {
      if (current.length) segments.push(current);
      current = [];
    } else current.push(p);
  }
  if (current.length) segments.push(current);

  const line = (seg: HealthPoint[]) => seg.map((p, i) => `${i ? "L" : "M"}${x(p.t).toFixed(1)},${y(p[field]!).toFixed(1)}`).join("");
  const area = (seg: HealthPoint[]) =>
    `${line(seg)}L${x(seg[seg.length - 1].t).toFixed(1)},${pad.top + h}L${x(seg[0].t).toFixed(1)},${pad.top + h}Z`;

  const latest = values.length ? values[values.length - 1] : null;
  const fmt = (v: number) => `${v.toFixed(digits)} ${unit}`;

  function onMove(e: React.PointerEvent<SVGSVGElement>) {
    if (!visible.length) return;
    const rect = e.currentTarget.getBoundingClientRect();
    const t = from + ((e.clientX - rect.left - pad.left) / w) * (to - from);
    let best = visible[0];
    for (const p of visible) if (Math.abs(p.t - t) < Math.abs(best.t - t)) best = p;
    setHover(best);
  }

  const ticks = [0, max / 2, max];
  const shown = hover && hover[field] != null ? hover : null;

  return (
    <div className="ogc-rise rounded-2xl bg-slate-800/60 p-4 shadow-lg ring-1 ring-white/5">
      <div className="mb-2 flex items-baseline justify-between gap-2">
        <h4 className="text-sm font-medium text-slate-300">{title}</h4>
        <span className="text-sm font-semibold text-white">
          {shown ? (
            <>
              {fmt(shown[field]!)} <span className="font-normal text-slate-500">um {clock(shown.t)}</span>
            </>
          ) : latest != null ? (
            fmt(latest)
          ) : (
            "—"
          )}
        </span>
      </div>
      <div ref={ref} className="relative">
        {width > 0 && (
          <svg
            width={width}
            height={height}
            className="block touch-none"
            onPointerMove={onMove}
            onPointerLeave={() => setHover(null)}
            role="img"
            aria-label={`${title}, Verlauf`}
          >
            {ticks.map((v) => (
              <g key={v}>
                <line x1={pad.left} x2={pad.left + w} y1={y(v)} y2={y(v)} stroke="currentColor" className="text-white/10" strokeWidth="1" />
                <text x={pad.left - 6} y={y(v)} dy="0.32em" textAnchor="end" className="fill-slate-500 text-[10px] tabular-nums">
                  {v >= 1000 ? `${(v / 1000).toFixed(1)}k` : Number.isInteger(v) ? v : v.toFixed(1)}
                </text>
              </g>
            ))}
            {[from, (from + to) / 2, to].map((t, i) => (
              <text key={t} x={x(t)} y={height - 4} textAnchor={i === 0 ? "start" : i === 2 ? "end" : "middle"} className="fill-slate-500 text-[10px] tabular-nums">
                {clock(t)}
              </text>
            ))}
            {segments.map((seg, i) => (
              <g key={i} className="text-ogc-teal">
                <path d={area(seg)} fill="currentColor" opacity="0.1" />
                <path d={line(seg)} pathLength={1} fill="none" stroke="currentColor" strokeWidth="2" strokeLinejoin="round" strokeLinecap="round" className="ogc-draw" />
              </g>
            ))}
            {shown && (
              <g className="text-ogc-teal">
                <line x1={x(shown.t)} x2={x(shown.t)} y1={pad.top} y2={pad.top + h} stroke="currentColor" className="text-white/30" strokeWidth="1" />
                <circle cx={x(shown.t)} cy={y(shown[field]!)} r="4.5" fill="currentColor" stroke="var(--color-slate-800)" strokeWidth="2" />
              </g>
            )}
            {!values.length && (
              <text x={pad.left + w / 2} y={pad.top + h / 2} textAnchor="middle" className="fill-slate-500 text-xs">
                Noch keine Messwerte
              </text>
            )}
          </svg>
        )}
      </div>
    </div>
  );
}

function OnlineStrip({ points, from, to }: { points: HealthPoint[]; from: number; to: number }) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<{ t: number; online: number | null } | null>(null);
  const visible = points.filter((p) => p.t >= from);
  const x = (t: number) => ((t - from) / (to - from)) * width;
  const step = 60;
  const offline = visible.filter((p) => p.online === 0).length;
  return (
    <div className="ogc-rise rounded-2xl bg-slate-800/60 p-4 shadow-lg ring-1 ring-white/5 md:col-span-2">
      <div className="mb-2 flex items-baseline justify-between gap-2">
        <h4 className="text-sm font-medium text-slate-300">Verbindung</h4>
        <span className="text-sm text-slate-400">
          {hover ? (
            <>
              {clock(hover.t)}: {hover.online == null ? "keine Messung" : hover.online ? "online" : "offline"}
            </>
          ) : offline ? (
            `${offline} Minute(n) offline`
          ) : visible.length ? (
            "durchgehend online"
          ) : (
            "noch keine Messwerte"
          )}
        </span>
      </div>
      <div ref={ref}>
        {width > 0 && (
          <svg width={width} height={22} className="block touch-none" role="img" aria-label="Verbindung im Zeitverlauf"
            onPointerMove={(e) => {
              const rect = e.currentTarget.getBoundingClientRect();
              const t = from + ((e.clientX - rect.left) / width) * (to - from);
              const p = visible.find((q) => Math.abs(q.t - t) <= step / 2);
              setHover({ t, online: p?.online ?? null });
            }}
            onPointerLeave={() => setHover(null)}
          >
            <rect x="0" y="4" width={width} height="14" rx="4" fill="currentColor" className="text-white/5" />
            {visible.map((p) =>
              p.online == null ? null : (
                <rect
                  key={p.t}
                  x={x(p.t - step)}
                  y="4"
                  width={Math.max(1, x(p.t) - x(p.t - step) - (p.online ? 0 : 1))}
                  height="14"
                  fill="currentColor"
                  className={p.online ? "text-emerald-500/70" : "text-red-500"}
                />
              ),
            )}
          </svg>
        )}
      </div>
      <div className="mt-2 flex gap-4 text-xs text-slate-500">
        <span className="inline-flex items-center gap-1.5"><span className="h-2.5 w-2.5 rounded-sm bg-emerald-500/70" /> online</span>
        <span className="inline-flex items-center gap-1.5"><span className="h-2.5 w-2.5 rounded-sm bg-red-500" /> offline</span>
        <span className="inline-flex items-center gap-1.5"><span className="h-2.5 w-2.5 rounded-sm bg-white/10" /> keine Messung</span>
      </div>
    </div>
  );
}

function HistorySection({ history }: { history: HealthHistory | null }) {
  const [range, setRange] = useState(RANGES[0].secs);
  const [table, setTable] = useState(false);
  const points = history?.points ?? [];
  // "Now" moves with each history refresh, not on every render.
  const to = useMemo(() => Math.floor(Date.now() / 1000), [history]);
  const from = to - range;
  const hasTemp = points.some((p) => p.temperature_c != null);
  return (
    <section className="mb-6">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
        <div>
          <h3 className="text-lg font-semibold text-white">Verlauf</h3>
          <p className="text-xs text-slate-500">Ein Messpunkt pro Minute, bis zu 24 h. Nach einem Neustart beginnt er neu.</p>
        </div>
        <div className="flex items-center gap-2">
          <div className="flex rounded-lg bg-slate-900/60 p-0.5 ring-1 ring-white/10" role="group" aria-label="Zeitraum">
            {RANGES.map((r) => (
              <button
                key={r.secs}
                type="button"
                onClick={() => setRange(r.secs)}
                aria-pressed={range === r.secs}
                className={`rounded-md px-3 py-1 text-sm transition-colors ${range === r.secs ? "bg-ogc-teal/20 font-semibold text-white" : "text-slate-400 hover:text-white"}`}
              >
                {r.label}
              </button>
            ))}
          </div>
          <button type="button" onClick={() => setTable((v) => !v)} className="rounded-lg border border-white/10 px-3 py-1 text-sm text-slate-300 hover:bg-white/5">
            {table ? "Diagramme" : "Tabelle"}
          </button>
        </div>
      </div>

      {table ? (
        <HistoryTable points={points.filter((p) => p.t >= from)} />
      ) : (
        <div className="grid gap-4 md:grid-cols-2">
          <OnlineStrip points={points} from={from} to={to} />
          <TrendChart title="Latenz" points={points} field="latency_ms" unit="ms" from={from} to={to} />
          <TrendChart title="CPU-Last" points={points} field="cpu_percent" unit="%" fixedMax={100} from={from} to={to} />
          <TrendChart title="Arbeitsspeicher" points={points} field="memory_percent" unit="%" fixedMax={100} from={from} to={to} />
          {hasTemp && <TrendChart title="CPU-Temperatur" points={points} field="temperature_c" unit="°C" fixedMax={90} from={from} to={to} digits={1} />}
        </div>
      )}
    </section>
  );
}

function HistoryTable({ points }: { points: HealthPoint[] }) {
  const rows = [...points].reverse().slice(0, 240);
  const cell = (v: number | null, digits = 0) => (v == null ? "—" : v.toFixed(digits));
  return (
    <div className="max-h-96 overflow-auto rounded-2xl bg-slate-800/60 ring-1 ring-white/5">
      <table className="w-full text-sm tabular-nums">
        <thead className="sticky top-0 bg-slate-800 text-left text-xs text-slate-400">
          <tr>
            {["Zeit", "Online", "Latenz (ms)", "CPU (%)", "RAM (%)", "Temp. (°C)", "Speicher (%)"].map((h) => (
              <th key={h} className="px-3 py-2 font-medium">{h}</th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-white/5">
          {rows.map((p) => (
            <tr key={p.t} className="text-slate-300">
              <td className="px-3 py-1.5">{clock(p.t)}</td>
              <td className="px-3 py-1.5">{p.online == null ? "—" : p.online ? "ja" : "nein"}</td>
              <td className="px-3 py-1.5">{cell(p.latency_ms)}</td>
              <td className="px-3 py-1.5">{cell(p.cpu_percent)}</td>
              <td className="px-3 py-1.5">{cell(p.memory_percent)}</td>
              <td className="px-3 py-1.5">{cell(p.temperature_c, 1)}</td>
              <td className="px-3 py-1.5">{cell(p.disk_percent)}</td>
            </tr>
          ))}
          {!rows.length && (
            <tr>
              <td colSpan={7} className="px-3 py-4 text-center text-slate-500">Noch keine Messwerte</td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}

// --- Monitoring access --------------------------------------------------------

function TokensCard() {
  const [tokens, setTokens] = useState<MonitorToken[]>([]);
  const [name, setName] = useState("");
  const [revealed, setRevealed] = useState<MonitorTokenCreated | null>(null);
  const [copied, setCopied] = useState(false);
  const [error, setError] = useState("");
  const origin = window.location.origin;

  const load = useCallback(() => {
    api<MonitorToken[]>("/api/monitor/tokens").then(setTokens).catch(() => setTokens([]));
  }, []);
  useEffect(load, [load]);

  async function create(e: FormEvent) {
    e.preventDefault();
    setError("");
    try {
      const t = await api<MonitorTokenCreated>("/api/monitor/tokens", {
        method: "POST",
        body: JSON.stringify({ name: name.trim() }),
      });
      setRevealed(t);
      setCopied(false);
      setName("");
      load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Token konnte nicht erzeugt werden");
    }
  }

  async function revoke(t: MonitorToken) {
    if (!window.confirm(`Token „${t.name}“ widerrufen? Abfragen damit schlagen sofort fehl.`)) return;
    await api(`/api/monitor/tokens/${t.id}`, { method: "DELETE" });
    if (revealed?.id === t.id) setRevealed(null);
    load();
  }

  async function copy(text: string) {
    const ok = await copyText(text);
    setCopied(ok);
    if (!ok) setError("Kopieren nicht möglich – Token bitte von Hand markieren und kopieren.");
  }

  const shownToken = revealed?.token ?? "<TOKEN>";
  return (
    <section className="ogc-rise rounded-2xl bg-slate-800/60 p-5 shadow-lg ring-1 ring-white/5">
      <h3 className="text-lg font-semibold text-white">Zugriff für Monitoring-Systeme</h3>
      <p className="mb-4 text-sm text-slate-400">
        Ein Token gibt nur Lesezugriff auf diese Werte — für Prometheus/Grafana, PRTG, Home Assistant oder eigene Skripte.
        Die Companion-App braucht keinen, sie nutzt die Anmeldung.
      </p>

      <form onSubmit={create} className="mb-3 flex flex-wrap gap-2">
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="Name, z. B. Grafana"
          maxLength={200}
          required
          className="min-w-0 flex-1 rounded-lg border border-white/10 bg-slate-900/60 px-3 py-2 text-sm outline-hidden focus:border-ogc-teal"
        />
        <button type="submit" className="rounded-lg border border-white/10 px-4 py-2 text-sm hover:bg-white/5">
          Token erzeugen
        </button>
      </form>
      {error && <div className="mb-3 rounded-lg bg-red-500/15 px-3 py-2 text-sm text-red-300">{error}</div>}

      {revealed && (
        <div className="mb-3 rounded-lg bg-slate-900/60 p-3 ring-1 ring-ogc-teal/40">
          <div className="text-xs text-slate-400">Token für „{revealed.name}“ jetzt kopieren — er wird nur einmal angezeigt:</div>
          <div className="mt-1 flex items-center gap-2">
            <code className="block min-w-0 flex-1 break-all text-sm text-ogc-teal">{revealed.token}</code>
            <button type="button" onClick={() => copy(revealed.token)} className="shrink-0 rounded-lg border border-white/10 px-3 py-1 text-xs hover:bg-white/5">
              {copied ? "Kopiert" : "Kopieren"}
            </button>
          </div>
        </div>
      )}

      {tokens.length > 0 && (
        <div className="mb-4 space-y-2">
          {tokens.map((t) => (
            <div key={t.id} className="flex flex-wrap items-center justify-between gap-3 rounded-xl bg-slate-900/40 p-3 text-sm ring-1 ring-white/5">
              <div className="min-w-0">
                <div className="truncate font-medium text-white">
                  {t.name} <span className="font-normal text-slate-500">…{t.hint}</span>
                </div>
                <div className="text-xs text-slate-500">
                  {t.last_used_at ? `zuletzt abgefragt ${new Date(t.last_used_at + "Z").toLocaleString()}` : "noch nie benutzt"}
                </div>
              </div>
              <button type="button" onClick={() => revoke(t)} className="rounded-lg border border-red-500/30 px-3 py-1 text-xs text-red-300 hover:bg-red-500/10">
                Widerrufen
              </button>
            </div>
          ))}
        </div>
      )}

      <details className="group text-sm">
        <summary className="cursor-pointer text-slate-300 hover:text-white">Endpunkte &amp; Beispiele</summary>
        <div className="mt-3 space-y-3 text-slate-400">
          <div>
            <div className="mb-1 font-medium text-slate-300">JSON (Skripte, Home Assistant, n8n)</div>
            <code className="block overflow-x-auto rounded-lg bg-slate-900/60 p-2 text-xs whitespace-pre text-slate-300">
              {`curl -H "Authorization: Bearer ${shownToken}" ${origin}/api/monitor`}
            </code>
          </div>
          <div>
            <div className="mb-1 font-medium text-slate-300">Prometheus</div>
            <code className="block overflow-x-auto rounded-lg bg-slate-900/60 p-2 text-xs whitespace-pre text-slate-300">
              {`- job_name: offgridcloud\n  metrics_path: /api/monitor/metrics\n  scheme: ${window.location.protocol.replace(":", "")}\n  authorization:\n    credentials: ${shownToken}\n  static_configs:\n    - targets: ["${window.location.host}"]`}
            </code>
          </div>
          <div>
            <div className="mb-1 font-medium text-slate-300">PRTG (HTTP Data Advanced)</div>
            <code className="block overflow-x-auto rounded-lg bg-slate-900/60 p-2 text-xs whitespace-pre text-slate-300">
              {`${origin}/api/monitor?format=prtg&token=${shownToken}`}
            </code>
          </div>
          <p className="text-xs text-slate-500">
            Ohne Token bleibt <code>/api/health</code> als einfacher Erreichbarkeits-Check offen.
          </p>
        </div>
      </details>
    </section>
  );
}

// --- Page ---------------------------------------------------------------------

export default function Health() {
  const live = usePolling<HealthSnapshot>("/api/monitor", LIVE_EVERY_MS);
  const history = usePolling<HealthHistory>("/api/monitor/history", HISTORY_EVERY_MS);
  const snap = live.data;

  return (
    <Layout>
      <div className="mb-6 flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 className="text-2xl font-bold sm:text-3xl">Zustand</h2>
          <p className="text-sm text-slate-400">System, Speicher und Internet auf einen Blick — aktualisiert alle 5 Sekunden.</p>
        </div>
        {snap && (
          <span className="inline-flex items-center gap-2 text-xs text-slate-500">
            <span className="h-1.5 w-1.5 rounded-full bg-ogc-teal motion-safe:animate-pulse" />
            Stand {new Date(snap.generated_at).toLocaleTimeString()}
          </span>
        )}
      </div>

      {live.error && (
        <div className="mb-4 rounded-lg bg-red-500/15 px-3 py-2 text-sm text-red-300">
          {live.error}
          {snap && " — angezeigt wird der letzte bekannte Stand."}
        </div>
      )}

      {!snap ? (
        !live.error && <div className="h-40 animate-pulse rounded-2xl bg-slate-800/60" />
      ) : (
        <>
          <Hero snap={snap} />

          <div className="mb-6 grid grid-cols-2 gap-4 lg:grid-cols-4">
            <Gauge
              label="CPU-Last"
              value={snap.system.cpu_percent}
              max={100}
              display={snap.system.cpu_percent != null ? `${snap.system.cpu_percent.toFixed(0)} %` : "—"}
              sub={snap.system.load_average ? `Last ${snap.system.load_average[0].toFixed(2)}` : undefined}
              status="ok"
            />
            <Gauge
              label="Arbeitsspeicher"
              value={snap.system.memory?.percent_used ?? null}
              max={100}
              display={snap.system.memory ? `${snap.system.memory.percent_used.toFixed(0)} %` : "—"}
              sub={snap.system.memory ? `${bytes(snap.system.memory.available)} frei` : undefined}
              status={rate(snap.system.memory?.percent_used, 90, 97)}
              delay={80}
            />
            <Gauge
              label="Temperatur"
              value={snap.system.temperature_c}
              max={90}
              display={snap.system.temperature_c != null ? `${snap.system.temperature_c.toFixed(0)} °C` : "—"}
              sub={snap.system.temperature_c == null ? "kein Sensor" : "CPU"}
              status={rate(snap.system.temperature_c, 70, 80)}
              delay={160}
            />
            <Gauge
              label="Speicher"
              value={snap.storage.buffer?.percent_used ?? null}
              max={100}
              display={snap.storage.buffer ? `${snap.storage.buffer.percent_used.toFixed(0)} %` : "—"}
              sub={snap.storage.buffer ? `${bytes(snap.storage.buffer.free)} frei` : undefined}
              status={rate(snap.storage.buffer?.percent_used, 85, 95)}
              delay={240}
            />
          </div>

          <section className="mb-6">
            <h3 className="mb-3 text-lg font-semibold text-white">Prüfungen</h3>
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
              {[...snap.checks]
                .sort((a, b) => ({ error: 0, warning: 1, ok: 2 })[a.status] - ({ error: 0, warning: 1, ok: 2 })[b.status])
                .map((c, i) => (
                  <CheckTile key={c.id} check={c} index={i} />
                ))}
            </div>
          </section>

          <div className="mb-6 grid gap-4 lg:grid-cols-2">
            <NetworkCard snap={snap} />
            <SystemCard snap={snap} />
          </div>
        </>
      )}

      <HistorySection history={history.data} />

      <TokensCard />
    </Layout>
  );
}
