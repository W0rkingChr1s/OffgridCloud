import { useEffect, useState } from "react";

const KEY = "ogc_retro";
const KONAMI = [
  "ArrowUp",
  "ArrowUp",
  "ArrowDown",
  "ArrowDown",
  "ArrowLeft",
  "ArrowRight",
  "ArrowLeft",
  "ArrowRight",
  "b",
  "a",
];

function apply(on: boolean) {
  document.documentElement.classList.toggle("retro", on);
}

/**
 * Hidden 80s-terminal easter egg. Toggle with the Konami code
 * (↑ ↑ ↓ ↓ ← → ← → B A). State persists in localStorage.
 */
export function RetroEasterEgg() {
  const [on, setOn] = useState(() => localStorage.getItem(KEY) === "1");
  const [toast, setToast] = useState(false);

  useEffect(() => {
    apply(on);
  }, [on]);

  useEffect(() => {
    let progress = 0;
    function onKey(e: KeyboardEvent) {
      const expected = KONAMI[progress];
      if (e.key.toLowerCase() === expected.toLowerCase()) {
        progress += 1;
        if (progress === KONAMI.length) {
          progress = 0;
          setOn((prev) => {
            const next = !prev;
            localStorage.setItem(KEY, next ? "1" : "0");
            if (next) {
              setToast(true);
              setTimeout(() => setToast(false), 2500);
            }
            return next;
          });
        }
      } else {
        progress = e.key === KONAMI[0] ? 1 : 0;
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  function disable() {
    localStorage.setItem(KEY, "0");
    setOn(false);
  }

  return (
    <>
      {toast && <div className="retro-toast">&gt; RETRO MODE ENGAGED -- OFFGRIDCLOUD v0.0.1</div>}
      {on && (
        <div className="retro-badge" onClick={disable} title="Klicken zum Beenden">
          RETRO
        </div>
      )}
    </>
  );
}
