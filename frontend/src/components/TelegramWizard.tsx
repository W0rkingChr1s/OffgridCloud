import { useEffect, useState, type ReactNode } from "react";
import {
  api,
  ApiError,
  type SystemStatus,
  type TelegramChat,
  type TelegramChats,
  type TelegramNames,
  type TelegramTokenCheck,
} from "../api";
import { useToast } from "../toast";

/**
 * Guided Telegram setup: name ideas for @BotFather, token check, then the chat
 * is picked from the ones that just wrote to the bot (no chat IDs to type).
 */

const STEPS = ["Bot anlegen", "Token", "Chat", "Fertig"] as const;
const CHAT_POLL_MS = 3000;

const inputCls =
  "w-full rounded-lg border border-white/10 bg-slate-900/60 px-3 py-2 text-sm outline-hidden focus:border-ogc-teal";
const btnCls =
  "rounded-lg bg-linear-to-r from-ogc-teal to-ogc-blue px-4 py-2 text-sm font-semibold text-white disabled:opacity-50";
const ghostCls =
  "rounded-lg px-3 py-2 text-sm text-slate-300 ring-1 ring-white/10 hover:bg-white/5 disabled:opacity-40";

/** navigator.clipboard only exists on HTTPS/localhost; the box is often plain HTTP. */
async function copyText(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    /* fall through to the legacy path */
  }
  const area = document.createElement("textarea");
  area.value = text;
  area.setAttribute("readonly", "");
  area.style.position = "fixed";
  area.style.opacity = "0";
  document.body.appendChild(area);
  area.select();
  let ok = false;
  try {
    ok = document.execCommand("copy");
  } catch {
    ok = false;
  }
  area.remove();
  return ok;
}

function CopyButton({ text, label = "Kopieren" }: { text: string; label?: string }) {
  const [done, setDone] = useState(false);
  return (
    <button
      type="button"
      onClick={async (e) => {
        e.stopPropagation();
        if (await copyText(text)) {
          setDone(true);
          window.setTimeout(() => setDone(false), 1500);
        }
      }}
      className="shrink-0 rounded-md px-2 py-1 text-xs text-ogc-teal ring-1 ring-ogc-teal/30 hover:bg-ogc-teal/10"
    >
      {done ? "✓ kopiert" : label}
    </button>
  );
}

/** A selectable suggestion row: click picks it, the button copies it. */
function Pick({ value, chosen, onChoose }: { value: string; chosen: boolean; onChoose: () => void }) {
  return (
    <div
      role="radio"
      aria-checked={chosen}
      tabIndex={0}
      onClick={onChoose}
      onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && onChoose()}
      className={`flex cursor-pointer items-center gap-2 rounded-lg px-3 py-2 text-sm ring-1 ${
        chosen ? "bg-ogc-teal/10 text-white ring-ogc-teal/50" : "text-slate-300 ring-white/10 hover:bg-white/5"
      }`}
    >
      <span aria-hidden className={chosen ? "text-ogc-teal" : "text-slate-600"}>
        {chosen ? "●" : "○"}
      </span>
      <code className="min-w-0 flex-1 truncate">{value}</code>
      <CopyButton text={value} />
    </div>
  );
}

function Step({ n, children }: { n: number; children: ReactNode }) {
  return (
    <li className="flex gap-3">
      <span className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-slate-700 text-xs text-slate-200">
        {n}
      </span>
      <div className="min-w-0 flex-1 text-sm text-slate-300">{children}</div>
    </li>
  );
}

const CHAT_KIND: Record<string, string> = {
  private: "Privatchat",
  group: "Gruppe",
  supergroup: "Gruppe",
  channel: "Kanal",
};

export default function TelegramWizard({
  status,
  onClose,
  onSaved,
}: {
  status: SystemStatus;
  onClose: () => void;
  onSaved: (s: SystemStatus) => void;
}) {
  const toast = useToast();
  const [step, setStep] = useState(() =>
    status.telegram_configured && !status.telegram_chat_id ? 2 : 0,
  );
  const [tokenSaved, setTokenSaved] = useState(status.telegram_configured);

  // Step 1: names
  const [label, setLabel] = useState<string | null>(null);
  const [names, setNames] = useState<TelegramNames | null>(null);
  const [displayName, setDisplayName] = useState("");
  const [username, setUsername] = useState("");

  // Step 2: token
  const [token, setToken] = useState("");
  const [check, setCheck] = useState<TelegramTokenCheck | null>(null);
  const [busy, setBusy] = useState(false);
  const [botUser, setBotUser] = useState("");

  // Step 3: chat
  const [chatMode, setChatMode] = useState<"private" | "group">("private");
  const [chats, setChats] = useState<TelegramChat[]>([]);
  const [conflict, setConflict] = useState(false);
  const [manualId, setManualId] = useState("");
  const [chosenChat, setChosenChat] = useState<TelegramChat | null>(null);

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  // Name ideas follow the label (debounced); the first fetch uses the hostname.
  useEffect(() => {
    const t = window.setTimeout(
      () => {
        const q = label === null ? "" : `?label=${encodeURIComponent(label)}`;
        api<TelegramNames>(`/api/system/telegram/names${q}`)
          .then((n) => {
            setNames(n);
            if (label === null) setLabel(n.label);
            setDisplayName(n.display_names[0] ?? "");
            setUsername(n.usernames[0] ?? "");
          })
          .catch(() => undefined);
      },
      label === null ? 0 : 300,
    );
    return () => window.clearTimeout(t);
  }, [label]);

  // Coming back with a stored token: learn the bot's @username for the links.
  useEffect(() => {
    if (step !== 2 || botUser || !tokenSaved) return;
    api<TelegramTokenCheck>("/api/system/telegram/check-token", { method: "POST", body: "{}" })
      .then((r) => r.ok && setBotUser(r.username))
      .catch(() => undefined);
  }, [step, botUser, tokenSaved]);

  // While on the chat step, list whoever writes to the bot.
  useEffect(() => {
    if (step !== 2) return;
    let alive = true;
    const poll = () =>
      api<TelegramChats>("/api/system/telegram/chats")
        .then((r) => {
          if (!alive) return;
          setChats(r.chats);
          setConflict(r.conflict);
        })
        .catch(() => undefined);
    poll();
    const id = window.setInterval(poll, CHAT_POLL_MS);
    return () => {
      alive = false;
      window.clearInterval(id);
    };
  }, [step]);

  async function put(patch: Record<string, unknown>): Promise<boolean> {
    try {
      const s = await api<SystemStatus>("/api/system", { method: "PUT", body: JSON.stringify(patch) });
      onSaved(s);
      return true;
    } catch (e) {
      toast.error("Telegram", e instanceof ApiError ? e.message : "Speichern fehlgeschlagen");
      return false;
    }
  }

  async function checkToken() {
    setBusy(true);
    setCheck(null);
    try {
      const r = await api<TelegramTokenCheck>("/api/system/telegram/check-token", {
        method: "POST",
        body: JSON.stringify({ token }),
      });
      setCheck(r);
      if (r.ok) setBotUser(r.username);
    } catch (e) {
      setCheck({ ok: false, message: e instanceof ApiError ? e.message : "Prüfung fehlgeschlagen", username: "", name: "" });
    } finally {
      setBusy(false);
    }
  }

  async function saveToken() {
    setBusy(true);
    // A new bot starts without a chat: the old chat belongs to the old bot.
    const ok = await put({ telegram_bot_token: token.trim(), telegram_chat_id: "" });
    setBusy(false);
    if (ok) {
      setTokenSaved(true);
      setToken("");
      setStep(2);
    }
  }

  async function useChat(chat: TelegramChat) {
    setBusy(true);
    const ok = await put({ telegram_chat_id: chat.id });
    setBusy(false);
    if (ok) {
      setChosenChat(chat);
      setStep(3);
    }
  }

  async function sendTest() {
    setBusy(true);
    try {
      const r = await api<{ sent: boolean; message: string }>("/api/system/telegram/test", { method: "POST" });
      if (r.sent) toast.success("Telegram", r.message);
      else toast.error("Telegram", r.message);
    } catch (e) {
      toast.error("Telegram", e instanceof ApiError ? e.message : "Test fehlgeschlagen");
    } finally {
      setBusy(false);
    }
  }

  const botLink = botUser ? `https://t.me/${botUser}` : "";
  const startLink =
    botUser && (chatMode === "private" ? `${botLink}?start=setup` : `${botLink}?startgroup=setup`);
  const reachable = (i: number) => i <= 1 || tokenSaved;

  return (
    <div className="fixed inset-0 z-40 flex items-center justify-center bg-black/60 p-4" onClick={onClose}>
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="tg-wizard-title"
        className="flex max-h-[92vh] w-full max-w-xl flex-col rounded-2xl bg-slate-900 ring-1 ring-white/10"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="border-b border-white/5 p-5 pb-4">
          <div className="flex items-center gap-3">
            <h3 id="tg-wizard-title" className="text-lg font-semibold text-white">
              Telegram-Bot einrichten
            </h3>
            <button type="button" onClick={onClose} aria-label="Schließen" className="ml-auto text-slate-500 hover:text-slate-300">
              ✕
            </button>
          </div>
          <ol className="mt-3 flex gap-1.5">
            {STEPS.map((s, i) => (
              <li key={s} className="flex-1">
                <button
                  type="button"
                  disabled={!reachable(i) || i === 3}
                  onClick={() => setStep(i)}
                  className={`w-full border-t-2 pt-1.5 text-left text-xs ${
                    i === step
                      ? "border-ogc-teal text-white"
                      : i < step
                        ? "border-ogc-teal/40 text-slate-400"
                        : "border-white/10 text-slate-500"
                  }`}
                >
                  {i + 1}. {s}
                </button>
              </li>
            ))}
          </ol>
        </div>

        <div className="overflow-y-auto p-5">
          {step === 0 && (
            <div className="grid gap-4">
              <p className="text-sm text-slate-400">
                Jede Box bekommt ihren <strong className="text-slate-200">eigenen Bot</strong> – Telegram lässt pro
                Bot nur eine Box Befehle abholen. Angelegt wird er bei <strong className="text-slate-200">@BotFather</strong>,
                dem offiziellen Bot von Telegram. Dauert eine Minute.
              </p>

              <label className="block text-sm">
                <span className="mb-1 block text-slate-400">Wie heißt die Box bzw. das Team?</span>
                <input
                  className={inputCls}
                  placeholder="z. B. Boot Nord, OV Musterstadt, Kamera 2"
                  value={label ?? ""}
                  maxLength={64}
                  onChange={(e) => setLabel(e.target.value)}
                />
              </label>

              {names && (
                <>
                  <div>
                    <div className="mb-1.5 text-xs font-medium text-slate-400">
                      Anzeigename <span className="font-normal text-slate-500">(frei wählbar, darf Leerzeichen enthalten)</span>
                    </div>
                    <div className="grid gap-1.5" role="radiogroup">
                      {names.display_names.map((n) => (
                        <Pick key={n} value={n} chosen={n === displayName} onChoose={() => setDisplayName(n)} />
                      ))}
                    </div>
                  </div>
                  <div>
                    <div className="mb-1.5 text-xs font-medium text-slate-400">
                      Benutzername{" "}
                      <span className="font-normal text-slate-500">
                        (weltweit eindeutig, endet auf „bot“ – ist einer vergeben, nimm den nächsten)
                      </span>
                    </div>
                    <div className="grid gap-1.5" role="radiogroup">
                      {names.usernames.map((n) => (
                        <Pick key={n} value={n} chosen={n === username} onChoose={() => setUsername(n)} />
                      ))}
                    </div>
                  </div>
                </>
              )}

              <ol className="grid gap-3 rounded-xl bg-slate-800/50 p-4">
                <Step n={1}>
                  <a
                    href="https://t.me/BotFather"
                    target="_blank"
                    rel="noreferrer"
                    className="font-medium text-ogc-teal hover:underline"
                  >
                    @BotFather in Telegram öffnen ↗
                  </a>{" "}
                  und auf <em>Start</em> tippen.
                </Step>
                <Step n={2}>
                  <span className="mr-2">Senden:</span>
                  <code className="rounded bg-slate-900 px-1.5 py-0.5">/newbot</code>{" "}
                  <CopyButton text="/newbot" />
                </Step>
                <Step n={3}>
                  <span className="mr-2">Nach dem Namen gefragt:</span>
                  <code className="rounded bg-slate-900 px-1.5 py-0.5 break-all">{displayName || "…"}</code>{" "}
                  {displayName && <CopyButton text={displayName} />}
                </Step>
                <Step n={4}>
                  <span className="mr-2">Nach dem Benutzernamen gefragt:</span>
                  <code className="rounded bg-slate-900 px-1.5 py-0.5 break-all">{username || "…"}</code>{" "}
                  {username && <CopyButton text={username} />}
                  <div className="mt-1 text-xs text-slate-500">
                    „Sorry, this username is already taken“? Einfach den nächsten Vorschlag schicken.
                  </div>
                </Step>
                <Step n={5}>
                  BotFather antwortet mit „Done! … Use this token to access the HTTP API“ und einem langen
                  Token. Den kopierst du im nächsten Schritt hier hinein.
                </Step>
              </ol>

              <details className="rounded-xl bg-slate-800/30 px-4 py-3 text-sm text-slate-400">
                <summary className="cursor-pointer text-slate-300">Optional: Profilbild setzen</summary>
                <p className="mt-2">
                  Bei @BotFather <code className="rounded bg-slate-900 px-1">/setuserpic</code> senden, den Bot wählen
                  und dieses Logo schicken:{" "}
                  <a href="/icon-512.png" download="offgridcloud-bot.png" className="text-ogc-teal hover:underline">
                    Logo herunterladen
                  </a>
                  . Befehlsmenü und Beschreibung richtet die Box selbst ein.
                </p>
              </details>

              <div className="flex flex-wrap justify-end gap-2">
                {tokenSaved && (
                  <button type="button" className={ghostCls} onClick={() => setStep(2)}>
                    Bot behalten, nur Chat wählen
                  </button>
                )}
                <button type="button" className={btnCls} onClick={() => setStep(1)}>
                  Bot ist angelegt – weiter
                </button>
              </div>
            </div>
          )}

          {step === 1 && (
            <div className="grid gap-4">
              <label className="block text-sm">
                <span className="mb-1 block text-slate-400">Token von @BotFather</span>
                <input
                  className={`${inputCls} font-mono`}
                  placeholder="123456789:AAH…"
                  autoComplete="off"
                  spellCheck={false}
                  value={token}
                  onChange={(e) => {
                    setToken(e.target.value);
                    setCheck(null);
                  }}
                  onKeyDown={(e) => e.key === "Enter" && token.trim() && checkToken()}
                />
              </label>
              <p className="text-xs text-slate-500">
                Der Token ist das Passwort des Bots: nicht weitergeben. Er wird verschlüsselt auf der Box gespeichert
                und nie wieder angezeigt. Verloren? Bei @BotFather mit <code>/token</code> erneut abrufen.
              </p>

              {check && (
                <div
                  className={`rounded-lg px-3 py-2 text-sm ring-1 ${
                    check.ok
                      ? "bg-emerald-500/10 text-emerald-200 ring-emerald-500/30"
                      : "bg-red-500/10 text-red-200 ring-red-500/30"
                  }`}
                >
                  {check.ok ? "✓ " : "✕ "}
                  {check.message}
                </div>
              )}

              <div className="flex flex-wrap justify-between gap-2">
                <button type="button" className={ghostCls} onClick={() => setStep(0)}>
                  Zurück
                </button>
                <div className="flex flex-wrap gap-2">
                  <button type="button" className={ghostCls} disabled={!token.trim() || busy} onClick={checkToken}>
                    {busy && !check ? "Prüfe …" : "Prüfen"}
                  </button>
                  <button
                    type="button"
                    className={btnCls}
                    disabled={!token.trim() || busy}
                    onClick={saveToken}
                    title={check?.ok ? undefined : "Speichert auch ungeprüft, z. B. wenn die Box gerade offline ist"}
                  >
                    {check?.ok ? "Speichern & weiter" : "Ungeprüft speichern"}
                  </button>
                </div>
              </div>
            </div>
          )}

          {step === 2 && (
            <div className="grid gap-4">
              <div>
                <div className="mb-1.5 text-sm text-slate-400">Wohin sollen die Meldungen gehen?</div>
                <div className="grid grid-cols-2 gap-2">
                  {(
                    [
                      ["private", "👤 Nur an mich", "Privatchat mit dem Bot"],
                      ["group", "👥 An eine Gruppe", "Das ganze Team liest mit"],
                    ] as const
                  ).map(([mode, title, sub]) => (
                    <button
                      key={mode}
                      type="button"
                      onClick={() => setChatMode(mode)}
                      className={`rounded-lg px-3 py-2 text-left text-sm ring-1 ${
                        chatMode === mode ? "bg-ogc-teal/10 text-white ring-ogc-teal/50" : "text-slate-300 ring-white/10 hover:bg-white/5"
                      }`}
                    >
                      <div className="font-medium">{title}</div>
                      <div className="text-xs text-slate-500">{sub}</div>
                    </button>
                  ))}
                </div>
              </div>

              <ol className="grid gap-3 rounded-xl bg-slate-800/50 p-4">
                {chatMode === "private" ? (
                  <Step n={1}>
                    {startLink ? (
                      <a href={startLink} target="_blank" rel="noreferrer" className="font-medium text-ogc-teal hover:underline">
                        Chat mit @{botUser} öffnen ↗
                      </a>
                    ) : (
                      "Deinen neuen Bot in Telegram öffnen"
                    )}{" "}
                    und auf <em>Start</em> tippen.
                  </Step>
                ) : (
                  <>
                    <Step n={1}>
                      {startLink ? (
                        <a href={startLink} target="_blank" rel="noreferrer" className="font-medium text-ogc-teal hover:underline">
                          @{botUser} zu einer Gruppe hinzufügen ↗
                        </a>
                      ) : (
                        "Den Bot in Telegram zu einer Gruppe hinzufügen"
                      )}{" "}
                      – bestehende Gruppe wählen oder vorher eine neue anlegen.
                    </Step>
                    <Step n={2}>
                      Erscheint die Gruppe unten nicht, in der Gruppe{" "}
                      <code className="rounded bg-slate-900 px-1">/start{botUser ? `@${botUser}` : ""}</code> senden.
                    </Step>
                    <Step n={3}>
                      <span className="text-slate-400">
                        Tipp: Macht ihr den Bot in der Gruppe zum Admin (Infos ändern, Nachrichten anheften), benennt er
                        sie passend um, setzt das Logo und pinnt die Begrüßung.
                      </span>
                    </Step>
                  </>
                )}
              </ol>

              {conflict && (
                <p className="rounded-lg bg-amber-500/10 px-3 py-2 text-xs text-amber-300 ring-1 ring-amber-500/30">
                  Eine andere Box nutzt denselben Bot-Token – Nachrichten an den Bot landen womöglich dort. Für diese Box
                  einen eigenen Bot anlegen (Schritt 1).
                </p>
              )}

              <div>
                <div className="mb-1.5 flex items-center gap-2 text-sm text-slate-400">
                  Erkannte Chats
                  <span className="inline-flex items-center gap-1 text-xs text-slate-500">
                    <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-ogc-teal" /> wartet auf Nachricht …
                  </span>
                </div>
                {chats.length === 0 ? (
                  <div className="rounded-lg border border-dashed border-white/10 px-3 py-4 text-center text-sm text-slate-500">
                    Noch nichts angekommen. Sobald du dem Bot schreibst, erscheint der Chat hier.
                  </div>
                ) : (
                  <ul className="grid gap-1.5">
                    {chats.map((c) => (
                      <li key={c.id} className="flex items-center gap-3 rounded-lg px-3 py-2 ring-1 ring-white/10">
                        <div className="min-w-0 flex-1">
                          <div className="truncate text-sm text-slate-200">{c.title}</div>
                          <div className="text-xs text-slate-500">
                            {CHAT_KIND[c.type] ?? c.type} · {c.id}
                            {c.id === status.telegram_chat_id && " · aktuell eingetragen"}
                          </div>
                        </div>
                        <button type="button" className={btnCls} disabled={busy} onClick={() => useChat(c)}>
                          Übernehmen
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
              </div>

              <details className="text-sm text-slate-400">
                <summary className="cursor-pointer">Chat-ID von Hand eintragen</summary>
                <div className="mt-2 flex gap-2">
                  <input
                    className={inputCls}
                    placeholder="z. B. 123456789 oder -100…"
                    value={manualId}
                    onChange={(e) => setManualId(e.target.value)}
                  />
                  <button
                    type="button"
                    className={ghostCls}
                    disabled={!/^-?\d+$/.test(manualId.trim()) || busy}
                    onClick={() => useChat({ id: manualId.trim(), type: "", title: manualId.trim(), seen_at: 0 })}
                  >
                    Übernehmen
                  </button>
                </div>
              </details>

              <div className="flex justify-start">
                <button type="button" className={ghostCls} onClick={() => setStep(1)}>
                  Zurück
                </button>
              </div>
            </div>
          )}

          {step === 3 && (
            <div className="grid gap-4 text-sm text-slate-300">
              <div className="rounded-lg bg-emerald-500/10 px-4 py-3 text-emerald-200 ring-1 ring-emerald-500/30">
                ✅ Verbunden{chosenChat?.title ? <> mit <strong>{chosenChat.title}</strong></> : null}. Der Bot meldet sich
                gleich mit „OffgridCloud verbunden“ im Chat und richtet sein Befehlsmenü ein.
              </div>
              <p className="text-slate-400">
                Ab jetzt kommen Statusmeldungen dorthin (welche, stellst du unter „Benachrichtigungen“ ein). Probier im
                Chat <code>/status</code>, <code>/queue</code> oder <code>/hilfe</code>.
              </p>
              <div className="flex flex-wrap justify-end gap-2">
                <button type="button" className={ghostCls} disabled={busy} onClick={sendTest}>
                  {busy ? "Sende …" : "Testnachricht senden"}
                </button>
                <button type="button" className={btnCls} onClick={onClose}>
                  Fertig
                </button>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
