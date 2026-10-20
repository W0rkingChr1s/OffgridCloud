import type { BondingLink, Uplink } from "../api";

/** What net-merge does right now, as far as the diagram cares. */
export interface BondingView {
  /** Bundling switched on and net-merge installed. */
  enabled: boolean;
  links: BondingLink[];
  /** Box-wide reason uploads go direct anyway ("" = they don't). */
  bypass: string;
}

type LinkState = "bundled" | "primary" | "reserve" | "skipped";

const STATE_LABEL: Record<LinkState, string> = {
  bundled: "gebündelt",
  primary: "aktiv",
  reserve: "Reserve",
  skipped: "nicht gebündelt",
};

// Line colour per state; "reserve"/"skipped" stay neutral and dashed.
const STATE_COLOR: Record<LinkState, string> = {
  bundled: "text-sky-400",
  primary: "text-emerald-400",
  reserve: "text-slate-600",
  skipped: "text-slate-600",
};

const KIND_NAME: Record<Uplink["kind"], string> = {
  ethernet: "Kabel",
  client: "WLAN",
  tether: "USB-Tethering",
};

function stateOf(link: Uplink, bonding: BondingView | undefined): { state: LinkState; note: string } {
  const seen = bonding?.links.find((b) => b.name === link.device);
  if (bonding?.enabled && !bonding.bypass && seen) {
    return seen.used ? { state: "bundled", note: "" } : { state: "skipped", note: seen.detail };
  }
  return { state: link.primary ? "primary" : "reserve", note: "" };
}

/**
 * Box → links → Internet. Shows which uplinks carry the uploads: the one with
 * the default route, or — with link bundling — every link net-merge uses at
 * the same time. Plain HTML/CSS (no SVG) so it reflows at phone width.
 */
export default function UplinkDiagram({ links, bonding }: { links: Uplink[]; bonding?: BondingView }) {
  if (links.length === 0) return null;
  const rows = links.map((l) => ({ link: l, ...stateOf(l, bonding) }));
  const bundled = rows.filter((r) => r.state === "bundled").length;

  let summary: string;
  if (bundled > 1) summary = `Uploads laufen parallel über ${bundled} Leitungen.`;
  else if (bonding?.enabled && bonding.bypass) summary = `Bündelung gerade ausgesetzt: ${bonding.bypass}. Uploads gehen über die aktive Leitung.`;
  else if (bundled === 1) summary = "Bündelung an, aber net-merge findet nur eine nutzbare Leitung.";
  else if (links.length > 1) summary = "Uploads nutzen eine Leitung, die anderen springen bei Ausfall ein.";
  else summary = "Eine Leitung ins Internet.";

  return (
    <div>
      <div className="flex items-stretch text-sm" role="img" aria-label={summary}>
        <Node title="Box" />
        <div className="flex min-w-0 flex-1 flex-col">
          {rows.map(({ link, state, note }, i) => (
            <div key={link.device} className="flex min-w-0 items-stretch">
              <Connector state={state} first={i === 0} last={i === rows.length - 1} side="left" />
              <div className="my-1 min-w-0 flex-1 rounded-xl bg-slate-900/60 px-3 py-2 ring-1 ring-white/10">
                <div className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-0.5">
                  <span className="truncate font-semibold text-white">{link.label}</span>
                  <span
                    className={`rounded-full px-1.5 py-px text-[11px] font-semibold ${
                      state === "bundled"
                        ? "bg-sky-500/20 text-sky-200"
                        : state === "primary"
                          ? "bg-emerald-500/20 text-emerald-200"
                          : "bg-white/5 text-slate-400"
                    }`}
                  >
                    {STATE_LABEL[state]}
                  </span>
                  {link.metered && (
                    <span className="rounded-full bg-amber-500/20 px-1.5 py-px text-[11px] font-semibold text-amber-200">
                      getaktet
                    </span>
                  )}
                </div>
                <div className="truncate text-xs text-slate-500">
                  {KIND_NAME[link.kind]} · {link.device}
                  {link.ip ? ` · ${link.ip}` : ""}
                  {note ? ` · ${note}` : ""}
                </div>
              </div>
              <Connector state={state} first={i === 0} last={i === rows.length - 1} side="right" />
            </div>
          ))}
        </div>
        <Node title="Internet" />
      </div>
      <p className="mt-2 text-xs text-slate-500">{summary}</p>
    </div>
  );
}

function Node({ title }: { title: string }) {
  return (
    <div className="flex shrink-0 items-center">
      <div className="rounded-xl bg-slate-700/60 px-2.5 py-2 text-xs font-semibold text-slate-200 ring-1 ring-white/10 sm:px-3 sm:text-sm">
        {title}
      </div>
    </div>
  );
}

/**
 * The line between a node and one link: a horizontal stub in the link's state
 * colour plus this row's share of the vertical bus that joins all links.
 */
function Connector({
  state,
  first,
  last,
  side,
}: {
  state: LinkState;
  first: boolean;
  last: boolean;
  side: "left" | "right";
}) {
  const flowing = state === "bundled" || state === "primary";
  const edge = side === "left" ? "left-0" : "right-0";
  return (
    <div className="relative w-4 shrink-0 sm:w-8">
      {!first && <span className={`absolute top-0 h-1/2 w-0.5 bg-slate-600 ${edge}`} />}
      {!last && <span className={`absolute bottom-0 h-1/2 w-0.5 bg-slate-600 ${edge}`} />}
      <span
        className={`absolute inset-x-0 top-1/2 -mt-px h-0.5 ${STATE_COLOR[state]} ${
          flowing ? "ogc-flow" : "ogc-idle"
        }`}
      />
    </div>
  );
}
