// Model sigils: the little character or glyph shown beside each model.
//   Anthropic: pixel-art critters, one accessory per Claude family.
//   OpenAI:    celestial glyphs for the Luna / Terra / Sol / Astra lines.
//   Others:    the vendor's logo, or a monogram when we have none.
// The critters and glyphs are original artwork for this dashboard.

const LOGOS = new Set([
  "anthropic", "openai", "amazon", "meta", "mistral", "deepseek", "qwen", "moonshotai",
  "xai", "zai", "cohere", "ai21", "nvidia", "google", "minimax",
]);
const MODEL_LOGOS = { anthropic: "claude", amazon: "nova", xai: "grok", moonshotai: "kimi" };

export function logoUrl(vendor) {
  return LOGOS.has(vendor) ? `assets/logos/${vendor}.svg` : null;
}

export function vendorLogo(vendor, name, size = 18) {
  const url = logoUrl(vendor);
  if (url) return `<img class="logo" src="${url}" width="${size}" height="${size}" alt="" loading="lazy">`;
  const letter = (name || vendor || "?").slice(0, 1).toUpperCase();
  return `<span class="monogram" style="--s:${size}px" aria-hidden="true">${letter}</span>`;
}

// ------------------------------------------------------------- Claude critters

const PIX = {
  o: "#D97757", d: "#B5573A", h: "#F2A585", k: "#1B0F0B", w: "#FFF3EA",
  g: "#7CCB8A", G: "#4E9E62", q: "#F6EADB", Q: "#C9B59A",
  y: "#F4C653", Y: "#C99A2E", r: "#E5484D",
  p: "#F6EADB", c: "#6B7CFF", l: "#B9A88F",
  m: "#B79CFF", s: "#FFFFFF", e: "#C9B6FF",
};

// Body spans columns 3-12; eyes sit at columns 5 and 10; arms reach columns 1-2 and 13-14.
const FACE = "ookooookoo";
const BODY = [
  "...." + "oooooooo" + "....",
  "..." + "ohoooooooo" + "...",
  "..." + FACE + "...",
  ".oo" + FACE + "oo.",
  ".oo" + "oooooooooo" + "oo.",
  "..." + "oooooooooo" + "...",
  "..." + "dddddddddd" + "...",
  "....d.d..d.d....",
  "....d.d..d.d....",
];

const HATS = {
  haiku: [
    "................",
    "................",
    ".........gg.....",
    "........gGg.....",
    "........G.......",
    "........G.......",
    "........G.......",
  ],
  sonnet: [
    "...........q....",
    "..........qQ....",
    ".........qQq....",
    "........qQq.....",
    ".......qQ.......",
    "......Q.........",
    "......Q.........",
  ],
  opus: [
    "................",
    "................",
    "....y..yy..y....",
    "....yy.yy.yy....",
    "....yyyyyyyy....",
    "....yryyyyry....",
    "....YYYYYYYY....",
  ],
  fable: [
    "................",
    "...pppp..pppp...",
    "...pllpccpllp...",
    "...ppppccpppp...",
    "...pllpccpllp...",
    "....pppccppp....",
    ".....cccccc.....",
  ],
  mythos: [
    ".......s........",
    "......ses.......",
    ".......s........",
    "..m..........m..",
    "..mm........mm..",
    "...mm......mm...",
    "....mmmmmmmm....",
  ],
  base: [
    "................",
    "................",
    "................",
    ".......hh.......",
    "......hhhh......",
    ".......hh.......",
    "................",
  ],
};

const EYES = { mythos: "m" };

function critterSvg(family) {
  const hat = HATS[family] || HATS.base;
  const rows = [...hat, ...BODY];
  const eye = EYES[family] || "k";
  let rects = "";
  rows.forEach((row, y) => {
    for (let x = 0; x < 16; x++) {
      let ch = row[x];
      if (ch === ".") continue;
      if (ch === "k") ch = eye;
      rects += `<rect x="${x}" y="${y}" width="1.02" height="1.02" fill="${PIX[ch]}"/>`;
    }
  });
  return `<svg viewBox="0 0 16 16" shape-rendering="crispEdges" aria-hidden="true">${rects}</svg>`;
}

// ------------------------------------------------------------- OpenAI celestial glyphs

let uid = 0;
function celestialSvg(family) {
  const id = `cg${++uid}`;
  const defs = `<defs>
    <radialGradient id="${id}a" cx="38%" cy="34%" r="70%"><stop offset="0" stop-color="#D9FFF0"/><stop offset=".55" stop-color="#5BE3B0"/><stop offset="1" stop-color="#0E7A58"/></radialGradient>
    <radialGradient id="${id}s" cx="50%" cy="50%" r="50%"><stop offset="0" stop-color="#FFF6D6"/><stop offset=".6" stop-color="#FFC857"/><stop offset="1" stop-color="#F08A24"/></radialGradient>
    <mask id="${id}m"><rect width="64" height="64" fill="#fff"/><circle cx="41" cy="25" r="17" fill="#000"/></mask>
  </defs>`;
  const twinkle = `<g fill="#E9FFF6"><circle cx="12" cy="14" r="1.1"/><circle cx="53" cy="50" r="1.3"/><circle cx="50" cy="12" r=".8"/><circle cx="10" cy="48" r=".8"/></g>`;
  let body = "";
  if (family === "luna") {
    body = `<circle cx="32" cy="33" r="19" fill="url(#${id}a)" mask="url(#${id}m)"/>
      <circle cx="24" cy="40" r="2.2" fill="#0E7A58" opacity=".45"/><circle cx="20" cy="30" r="1.4" fill="#0E7A58" opacity=".4"/>`;
  } else if (family === "sol") {
    let rays = "";
    for (let i = 0; i < 12; i++) {
      const a = (i * Math.PI) / 6;
      const r1 = 17, r2 = i % 2 ? 23 : 27;
      rays += `<line x1="${32 + r1 * Math.cos(a)}" y1="${32 + r1 * Math.sin(a)}" x2="${32 + r2 * Math.cos(a)}" y2="${32 + r2 * Math.sin(a)}"/>`;
    }
    body = `<g stroke="#FFC857" stroke-width="2.4" stroke-linecap="round">${rays}</g><circle cx="32" cy="32" r="13" fill="url(#${id}s)"/>`;
  } else if (family === "terra") {
    body = `<circle cx="32" cy="32" r="17" fill="url(#${id}a)"/>
      <g fill="none" stroke="#0B5E44" stroke-width="1.3" opacity=".7">
        <ellipse cx="32" cy="32" rx="7" ry="17"/><line x1="15" y1="32" x2="49" y2="32"/>
        <path d="M17.5 24h29M17.5 40h29"/></g>
      <ellipse cx="32" cy="32" rx="27" ry="8" fill="none" stroke="#9AF5D2" stroke-width="1.2" transform="rotate(-18 32 32)" opacity=".85"/>
      <circle cx="56" cy="24" r="2.4" fill="#E9FFF6"/>`;
  } else if (family === "astra") {
    body = `<path d="M32 6 C34 24 40 30 58 32 C40 34 34 40 32 58 C30 40 24 34 6 32 C24 30 30 24 32 6Z" fill="url(#${id}a)"/>
      <path d="M32 18 C33 28 36 31 46 32 C36 33 33 36 32 46 C31 36 28 33 18 32 C28 31 31 28 32 18Z" fill="#F2FFF9"/>`;
  }
  return `<svg viewBox="0 0 64 64" aria-hidden="true">${defs}${twinkle}${body}</svg>`;
}

// ------------------------------------------------------------- public

export function hasCharacter(model) {
  return (model.vendor === "anthropic") || (model.vendor === "openai" && !!model.family);
}

export function sigil(model, size = 32, opts = {}) {
  const cls = `sigil ${opts.float ? "float" : ""}`;
  if (model.vendor === "anthropic") {
    return `<span class="${cls} sigil-critter" style="--s:${size}px" title="${model.family ? cap(model.family) : "Claude"}">${critterSvg(model.family)}</span>`;
  }
  if (model.vendor === "openai" && model.family) {
    return `<span class="${cls} sigil-celestial" style="--s:${size}px" title="${cap(model.family)}">${celestialSvg(model.family)}</span>`;
  }
  const logo = MODEL_LOGOS[model.vendor];
  const inner = logo
    ? `<img class="logo" src="assets/logos/${logo}.svg" alt="" loading="lazy">`
    : logoUrl(model.vendor)
      ? `<img class="logo" src="${logoUrl(model.vendor)}" alt="" loading="lazy">`
      : `<span class="monogram" style="--s:${Math.round(size * 0.7)}px">${(model.vendorName || model.vendor).slice(0, 1).toUpperCase()}</span>`;
  return `<span class="${cls} sigil-logo" style="--s:${size}px">${inner}</span>`;
}

function cap(s) {
  return s ? s[0].toUpperCase() + s.slice(1) : s;
}
