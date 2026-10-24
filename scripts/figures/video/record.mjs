// Record a ~60 s walkthrough of the live dry-run dashboards into docs/research/figures/readme/.
//
// Prerequisite: both screeners running against mainnet (real data only):
//   python3 . v3 --port 8080 --hours 3      python3 . arb --port 8765 --hours 3
// Usage (playwright from any local install, e.g. NODE_PATH=<dir>/node_modules):
//   node scripts/figures/video/record.mjs [--v3 http://127.0.0.1:8080] [--arb http://127.0.0.1:8765]
import { execFileSync } from "node:child_process";
import { mkdirSync, mkdtempSync, readdirSync, renameSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { createRequire } from "node:module";
import { caption, cardHtml, clickTab } from "./overlay.mjs";

const require = createRequire(import.meta.url);
const { chromium } = require("playwright");
const REPO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../..");
const OUT = path.join(REPO, "docs/research/figures/readme");
const SIZE = { width: 1600, height: 900 };
const arg = (k, d) => { const i = process.argv.indexOf(k); return i > 0 ? process.argv[i + 1] : d; };
const V3 = arg("--v3", "http://127.0.0.1:8080");
const ARB = arg("--arb", "http://127.0.0.1:8765");
const hold = (page, s) => page.waitForTimeout(s * 1000);
const snap = async (base) => (await fetch(`${base}/api/snapshot`)).json();
const fmtInt = (n) => Math.round(n || 0).toLocaleString("en-US");

async function scenes(page) {
  const day = new Date().toISOString().slice(0, 10);
  await page.setContent(cardHtml({
    title: "trenches-eth",
    sub: `Live dry-run on Ethereum mainnet, ${day}. Real blocks, real swaps; nothing is signed or sent.`,
    note: "Thesis research (analysis only): how exposed are Uniswap traders to sandwich attacks?",
  }));
  await hold(page, 5);

  await page.goto(V3);
  await caption(page, "V3 screener: every confirmed block is decoded; rows are victims whose slippage room exceeds the round-trip fee");
  await hold(page, 20);
  await clickTab(page, "funnel");
  await caption(page, "Visibility funnel: most guards are hidden in aggregator or private-router calldata, many are zero");
  await hold(page, 18);

  await page.goto(ARB);
  await caption(page, "Cross-pool arbitrage scanner: WETH price gaps between Uniswap V2, V3 and SushiSwap markets");
  await hold(page, 9);

  const endSnap = await (await fetch(`${V3}/api/snapshot?parts=stats,strict`)).json();
  const end = endSnap.stats;
  const rows = endSnap.strict || [];
  const material = rows.filter((r) => r.mesp / 1e18 >= 0.001).length;
  const f = end.funnel || {};
  const seen = f.decoded || 0;
  const visible = (f.router_decoded || 0) + (f.aggregator_decoded || 0);
  await page.setContent(cardHtml({
    title: "This recording, measured",
    sub: `${fmtInt(end.blocks)} confirmed blocks decoded live, up to block ${fmtInt(end.block)} (${new Date().toISOString().slice(0, 16).replace("T", " ")} UTC)`,
    stats: [
      [fmtInt(seen), "V3 swaps decoded"],
      [seen ? ((100 * visible) / seen).toFixed(1) + "%" : "n/a", "with a readable guard"],
      [fmtInt(f.guardless), "set no guard at all"],
      [`${fmtInt(material)} of ${fmtInt(rows.length)}`, "exposure targets worth 0.001 ETH or more"],
    ],
    note: "Dry-run only. Sandwich attacks harm the traders they target; this measures, it does not trade.",
  }));
  await hold(page, 8);
}

async function main() {
  const tmp = mkdtempSync(path.join(os.tmpdir(), "rec-"));
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: SIZE, recordVideo: { dir: tmp, size: SIZE } });
  const page = await ctx.newPage();
  await scenes(page);
  await ctx.close();
  await browser.close();
  const webm = path.join(tmp, readdirSync(tmp).find((f) => f.endsWith(".webm")));
  mkdirSync(OUT, { recursive: true });
  const mp4 = path.join(OUT, "platform.mp4");
  execFileSync("ffmpeg", ["-y", "-loglevel", "error", "-i", webm, "-c:v", "libx264", "-preset", "slow",
    "-crf", "26", "-pix_fmt", "yuv420p", "-movflags", "+faststart", mp4]);
  execFileSync("ffmpeg", ["-y", "-loglevel", "error", "-i", mp4, "-vf",
    "fps=8,scale=960:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=96[p];[b][p]paletteuse=dither=bayer:bayer_scale=4",
    path.join(OUT, "platform-preview.gif")]);
  renameSync(webm, path.join(tmp, "raw.webm"));
  console.log(`wrote ${mp4} and platform-preview.gif`);
}

main().catch((e) => { console.error(e); process.exit(1); });
