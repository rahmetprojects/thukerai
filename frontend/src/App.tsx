/*
 src/App.tsx - the shell: ONE assistant in front, the machinery one click behind it
 ===============================================================================
   #ask     (default) the assistant: voice or text in, cited + explained answer out   (views/Assistant.tsx)
   #data    Under the hood: ask the data, catalogue, add a source, health             (views/Explore.tsx)
 Kept, not in the menu (each still works by its link, and each uses the same API):
   #check   the Trust Report on its own (views/Check.tsx)      #bank   a bank's payment screen using the API
   #guide   the earlier voice-only guide (views/Guide.tsx)
 Plus two touchpoints outside this app: the browser badge (extension/) and AI assistants (mcp_server.py).
 Routing is the URL hash, so every view can be linked and the server needs no routes for it.
 The header shows how fresh the official data is, because trust depends on it.
*/
import { useEffect, useState } from "react";
import * as api from "./lib/api";
import { Check } from "./views/Check";
import { Bank } from "./views/Bank";
import { Explore } from "./views/Explore";
import { Guide } from "./views/Guide";
import { Assistant } from "./views/Assistant";

export const BRAND = "BucketTreasure";          // working name: change it here
type Route = "ask" | "check" | "bank" | "data" | "guide";
const MENU: [Route, string][] = [["ask", "Assistant"], ["data", "Under the hood"]];
const ALL: Route[] = ["ask", "check", "bank", "data", "guide"];
const fromHash = (): Route => {
  const h = location.hash.replace(/^#\/?/, "").split(/[?&]/)[0] as Route;
  return ALL.includes(h) ? h : "ask";
};

export function App() {
  const [route, setRoute] = useState<Route>(fromHash());
  const [health, setHealth] = useState<{ datasets: number; available: number; failing: string[] } | null>(null);

  useEffect(() => {
    const on = () => setRoute(fromHash());
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  useEffect(() => { api.health().then((h) => setHealth(h.data)).catch(() => setHealth(null)); }, [route]);
  useEffect(() => { document.documentElement.dir = "ltr"; }, [route]);

  return (
    <div className={`app r-${route}`}>
      <header className="masthead">
        <a className="wordmark" href="#ask">{BRAND}</a>
        <nav aria-label="Main">
          {MENU.map(([r, label]) => (<a key={r} href={`#${r}`} className={route === r ? "on" : ""} aria-current={route === r ? "page" : undefined}>{label}</a>))}
        </nav>
        <a className={`freshness ${health && health.failing.length ? "warn" : ""}`} href="#data">
          {health ? `${health.available}/${health.datasets} official lists live${health.failing.length ? ` · ${health.failing.length} failing` : ""}` : "data status unknown"}
        </a>
      </header>
      <main className="main">
        {route === "ask" && <Assistant />}
        {route === "check" && <Check />}
        {route === "bank" && <Bank />}
        {route === "data" && <Explore />}
        {route === "guide" && <Guide />}
      </main>
    </div>);
}
