/*
 * moray-scribe interactive topology viewer.
 *
 * Zero dependencies: a hand-rolled force-directed layout on a 2D canvas.
 * Reads the SymbolGraph JSON written by `moray-scribe graph`, renders static
 * edges solid, AI-inferred edges as animated red dashed arrows and confirmed
 * ORM data flows as thick amber arrows, and shows a detail panel with source.
 */
"use strict";

const KIND_COLORS = {
  module: "#60a5fa",
  package: "#818cf8",
  class: "#34d399",
  function: "#a78bfa",
  method: "#c084fc",
  variable: "#94a3b8",
  constant: "#fbbf24",
  import: "#64748b",
  unknown: "#64748b",
};

const KIND_LABELS = {
  module: "模块",
  package: "包",
  class: "类",
  function: "函数",
  method: "方法",
  variable: "变量",
  constant: "常量",
  unknown: "未知",
};

const EDGE_LABELS = {
  calls: "调用",
  imports: "导入",
  inherits: "继承",
  contains: "包含",
  instantiates: "实例化",
  uses: "使用",
  references: "引用",
  data_flow: "数据流",
};

const COLOR = {
  static: "rgba(70, 112, 143, 0.55)",
  staticHl: "rgba(125, 183, 222, 0.95)",
  infer: "rgba(244, 63, 94, 0.9)",
  flow: "rgba(245, 158, 11, 0.92)",
  label: "rgba(231, 238, 251, 0.92)",
  labelDim: "rgba(139, 160, 194, 0.55)",
  route: "#22d3ee",
  orm: "#fb923c",
};

// ------------------------------------------------------------------ state

const state = {
  graph: null,
  nodes: [],
  edges: [],          // { source, target, kind, inferred, confidence, reason, weight }
  byId: new Map(),
  adjacency: new Map(), // id -> { out: [edge], in: [edge] }
  transform: { k: 1, x: 0, y: 0 },
  selected: null,
  hovered: null,
  dragging: null,     // { node, offsetX, offsetY, moved }
  panning: null,      // { startX, startY, originX, originY }
  pointer: { x: 0, y: 0 },
  query: "",
  hiddenKinds: new Set(),
  hideDead: false,
  alpha: 1,
  ticks: 0,
  needsFit: true,
  sourceCache: new Map(),
};

// `?debug=1` renders a live state panel that `--dump-dom` can read in
// headless screenshot runs; it also captures uncaught errors.
const PARAMS = new URLSearchParams(window.location.search);
const DEBUG = PARAMS.has("debug");
let debugEl = null;
if (DEBUG) {
  debugEl = document.createElement("pre");
  debugEl.id = "viewer-debug";
  debugEl.style.cssText =
    "position:absolute;left:0;top:64px;z-index:99;color:#22d3ee;font:10px monospace;" +
    "background:rgba(0,0,0,.65);padding:6px;max-width:640px;white-space:pre-wrap";
  document.body.appendChild(debugEl);
  window.addEventListener("error", (event) => {
    debugEl.textContent += `\nERROR: ${event.message} @${event.lineno}:${event.colno}`;
  });
}

const canvas = document.getElementById("graph");
const ctx = canvas.getContext("2d");
const tooltip = document.createElement("div");
tooltip.className = "tooltip";
document.body.appendChild(tooltip);

const dpr = () => Math.min(window.devicePixelRatio || 1, 2);

// ------------------------------------------------------------- loading

async function boot() {
  try {
    const config = await fetch("/api/config").then((r) => r.json());
    const graph = await fetch("/api/graph").then((r) => r.json());
    state.graph = graph;
    state.sourceRoot = config.source_root;
    document.getElementById("project-name").textContent =
      config.project || graph.metadata?.project || "project";
    normalize(graph);
    buildKindFilters();
    updateStats();
    applyDeepLink();
    startSimulation();
    document.getElementById("loader").classList.add("hidden");
    resize();
    // Fit once as soon as layout is measurable, so the graph is framed even if
    // a headless/preview environment throttles animation frames.
    fitToView(false);
    applyDeepLinkSelection();
    requestAnimationFrame(frame);
  } catch (error) {
    const loader = document.getElementById("loader");
    loader.innerHTML =
      '<span style="color:#fca5a5">加载失败：' +
      String(error && error.message ? error.message : error) +
      "</span>";
  }
}

function normalize(graph) {
  const nodes = Object.values(graph.nodes || {});
  const edges = (graph.edges || []).filter(
    (e) => graph.nodes[e.source] && graph.nodes[e.target]
  );

  const maxRank = Math.max(1e-9, ...nodes.map((n) => n.pagerank || 0));
  const degree = new Map();
  for (const edge of edges) {
    degree.set(edge.source, (degree.get(edge.source) || 0) + 1);
    degree.set(edge.target, (degree.get(edge.target) || 0) + 1);
  }
  const maxDegree = Math.max(1, ...degree.values());

  const golden = Math.PI * (3 - Math.sqrt(5));
  nodes.sort((a, b) => a.id.localeCompare(b.id));
  nodes.forEach((node, index) => {
    const rank = Math.sqrt((node.pagerank || 0) / maxRank);
    node._rank = rank;
    const deg = degree.get(node.id) || 0;
    node._radius = 4.5 + rank * 9 + Math.sqrt(deg / maxDegree) * 5;
    const angle = index * golden;
    const ring = 40 + Math.sqrt(index + 1) * 34;
    node._x = Math.cos(angle) * ring + (pseudo(index) - 0.5) * 30;
    node._y = Math.sin(angle) * ring + (pseudo(index + 7) - 0.5) * 30;
    node._vx = 0;
    node._vy = 0;
    node._alpha = 1;
    node._route = node.route && node.route.path ? node.route : null;
    node._orm = Array.isArray(node.tags) && node.tags.includes("orm_model");
    state.byId.set(node.id, node);
    state.adjacency.set(node.id, { out: [], in: [] });
  });

  for (const edge of edges) {
    edge._kind = edge.kind;
    state.adjacency.get(edge.source).out.push(edge);
    state.adjacency.get(edge.target).in.push(edge);
  }

  state.nodes = nodes;
  state.edges = edges;
}

function pseudo(index) {
  const x = Math.sin(index * 127.1 + 311.7) * 43758.5453;
  return x - Math.floor(x);
}

// -------------------------------------------------------------- deep links

/**
 * Apply viewer URL parameters so a state can be shared or reproduced:
 *
 *   ?settle=400            run the layout synchronously before first paint
 *   ?select=<symbol id>    select a node (exact id, or a unique suffix)
 *   ?hide_dead=1           hide dead-code candidates on load
 *   ?q=<text>              pre-fill the search box
 */
function applyDeepLink() {
  const settleSteps = parseInt(PARAMS.get("settle") || "0", 10);
  if (Number.isFinite(settleSteps) && settleSteps > 0) {
    for (let i = 0; i < settleSteps && state.alpha > 0.02; i += 1) {
      stepSimulation();
    }
    state.needsFit = false;
  }
  if (PARAMS.get("hide_dead") === "1") {
    state.hideDead = true;
    const toggle = document.querySelector("#toggle-dead");
    if (toggle) toggle.checked = true;
  }
  const query = PARAMS.get("q");
  if (query) {
    state.query = query.toLowerCase();
    const input = document.getElementById("search");
    if (input) input.value = query;
  }
}

/** Select the `?select=` target after the camera has been fitted. */
function applyDeepLinkSelection() {
  const wanted = PARAMS.get("select");
  if (!wanted) return;
  const exact = state.byId.get(wanted);
  const matches = exact
    ? [exact]
    : state.nodes.filter((node) => node.id.endsWith(wanted) || node.name === wanted);
  if (matches.length >= 1) {
    selectNode(matches[0]);
    focusNode(matches[0], true);
  }
}

// ----------------------------------------------------------- simulation

function startSimulation() {
  state.alpha = 1;
  state.ticks = 0;
  const settle = () => {
    if (state.alpha < 0.02 || state.ticks > 900) return;
    stepSimulation();
    state.ticks += 1;
    requestAnimationFrame(settle);
  };
  requestAnimationFrame(settle);
}

function stepSimulation() {
  const nodes = state.nodes;
  const n = nodes.length;
  const repulsion = 9000;
  const centerForce = 0.012;

  for (let i = 0; i < n; i += 1) {
    const a = nodes[i];
    for (let j = i + 1; j < n; j += 1) {
      const b = nodes[j];
      let dx = a._x - b._x;
      let dy = a._y - b._y;
      let dist2 = dx * dx + dy * dy;
      if (dist2 < 1) {
        dx = (pseudo(i + j) - 0.5) * 2;
        dy = (pseudo(i * j + 3) - 0.5) * 2;
        dist2 = 4;
      }
      const min = a._radius + b._radius + 16;
      const dist = Math.sqrt(dist2);
      // spring-like repulsion, stronger when nodes overlap
      const force = (repulsion / dist2) * (dist < min ? 2.2 : 1);
      const fx = (dx / dist) * force;
      const fy = (dy / dist) * force;
      a._vx += fx;
      a._vy += fy;
      b._vx -= fx;
      b._vy -= fy;
    }
  }

  for (const edge of state.edges) {
    if (edge.inferred) continue; // inferred links guide layout only weakly
    const a = state.byId.get(edge.source);
    const b = state.byId.get(edge.target);
    if (!a || !b) continue;
    const dx = b._x - a._x;
    const dy = b._y - a._y;
    const dist = Math.max(1, Math.hypot(dx, dy));
    const desired = 92;
    const force = (dist - desired) * 0.045;
    const fx = (dx / dist) * force;
    const fy = (dy / dist) * force;
    a._vx += fx;
    a._vy += fy;
    b._vx -= fx;
    b._vy -= fy;
  }

  for (const node of nodes) {
    node._vx -= node._x * centerForce;
    node._vy -= node._y * centerForce;
    node._vx *= 0.55;
    node._vy *= 0.55;
    node._x += node._vx * state.alpha;
    node._y += node._vy * state.alpha;
  }
  state.alpha *= 0.988;
  if (state.needsFit && state.alpha < 0.35) {
    fitToView(false);
    state.needsFit = false;
  }
}

// ------------------------------------------------------------- drawing

function resize() {
  const rect = canvas.getBoundingClientRect();
  const scale = dpr();
  canvas.width = Math.max(1, Math.floor(rect.width * scale));
  canvas.height = Math.max(1, Math.floor(rect.height * scale));
  ctx.setTransform(scale, 0, 0, scale, 0, 0);
}

function frame(timestamp) {
  draw(timestamp);
  if (DEBUG && debugEl) {
    const t = state.transform;
    debugEl.textContent = JSON.stringify({
      k: Number(t.k.toFixed(3)),
      x: Math.round(t.x),
      y: Math.round(t.y),
      alpha: Number(state.alpha.toFixed(3)),
      ticks: state.ticks,
      nodes: state.nodes.length,
      edges: state.edges.length,
      needsFit: state.needsFit,
      selected: state.selected ? state.selected.id : null,
    });
  }
  requestAnimationFrame(frame);
}

function isVisible(node) {
  if (state.hiddenKinds.has(node.kind)) return false;
  if (state.hideDead && node.is_dead_candidate) return false;
  return true;
}

function matchesQuery(node) {
  const q = state.query;
  if (!q) return true;
  const haystack = [
    node.id, node.name, node.module, node.signature || "",
    node._route ? node._route.path : "",
  ].join(" ").toLowerCase();
  return haystack.includes(q);
}

function draw(timestamp) {
  const { width, height } = canvas.getBoundingClientRect();
  ctx.clearRect(0, 0, width, height);

  // subtle dot grid in screen space
  drawGrid(width, height);

  ctx.save();
  ctx.translate(state.transform.x, state.transform.y);
  ctx.scale(state.transform.k, state.transform.k);

  const dimmedBySearch = state.query.length > 0;
  const edgeHighlight = state.selected || state.hovered;
  const neighbors = new Set();
  if (edgeHighlight) {
    neighbors.add(edgeHighlight.id);
    for (const edge of state.edges) {
      if (edge.source === edgeHighlight.id) neighbors.add(edge.target);
      if (edge.target === edgeHighlight.id) neighbors.add(edge.source);
    }
  }

  drawEdges(timestamp, neighbors, edgeHighlight);
  drawNodes(neighbors, edgeHighlight, dimmedBySearch);

  ctx.restore();
  drawTooltip();
}

function drawGrid(width, height) {
  const gap = 44 * state.transform.k;
  if (gap < 14) return;
  ctx.save();
  ctx.fillStyle = "rgba(90, 120, 170, 0.10)";
  const offsetX = state.transform.x % gap;
  const offsetY = state.transform.y % gap;
  for (let x = offsetX; x < width; x += gap) {
    for (let y = offsetY; y < height; y += gap) {
      ctx.fillRect(x, y, 1.1, 1.1);
    }
  }
  ctx.restore();
}

function drawEdges(timestamp, neighbors, highlight) {
  const focus = highlight ? highlight.id : null;
  for (const edge of state.edges) {
    const a = state.byId.get(edge.source);
    const b = state.byId.get(edge.target);
    if (!a || !b) continue;
    if (!isVisible(a) || !isVisible(b)) continue;

    const inferred = Boolean(edge.inferred);
    const dataflow = edge.kind === "data_flow";
    const connected =
      focus !== null && (edge.source === focus || edge.target === focus);
    const faded =
      focus !== null && !connected ? 0.16 : 1;

    let color = COLOR.static;
    let width = 1.05;
    if (inferred) {
      color = COLOR.infer;
      width = 1.35;
    }
    if (dataflow) {
      color = COLOR.flow;
      width = 2.2;
    }
    if (connected) {
      color = inferred ? COLOR.infer : dataflow ? COLOR.flow : COLOR.staticHl;
      width += 0.9;
    }

    ctx.save();
    ctx.globalAlpha = faded;
    ctx.strokeStyle = color;
    ctx.lineWidth = width / state.transform.k;
    ctx.setLineDash(inferred ? [7 / state.transform.k, 5 / state.transform.k] : []);
    if (inferred) ctx.lineDashOffset = -((timestamp || 0) / 45) % 12;

    const dx = b._x - a._x;
    const dy = b._y - a._y;
    const dist = Math.max(0.001, Math.hypot(dx, dy));
    const ux = dx / dist;
    const uy = dy / dist;
    const startX = a._x + ux * (a._radius + 3);
    const startY = a._y + uy * (a._radius + 3);
    const endX = b._x - ux * (b._radius + 6);
    const endY = b._y - uy * (b._radius + 6);

    ctx.beginPath();
    ctx.moveTo(startX, startY);
    ctx.lineTo(endX, endY);
    ctx.stroke();
    ctx.setLineDash([]);

    // arrow head
    const head = 7 / state.transform.k + 1.5;
    ctx.beginPath();
    ctx.fillStyle = color;
    ctx.moveTo(endX, endY);
    ctx.lineTo(
      endX - ux * head - uy * head * 0.45,
      endY - uy * head + ux * head * 0.45
    );
    ctx.lineTo(
      endX - ux * head + uy * head * 0.45,
      endY - uy * head - ux * head * 0.45
    );
    ctx.closePath();
    ctx.fill();
    ctx.restore();
  }
}

function drawNodes(neighbors, highlight, dimmedBySearch) {
  for (const node of state.nodes) {
    if (!isVisible(node)) continue;
    const dim =
      (highlight && !neighbors.has(node.id)) ||
      (dimmedBySearch && !matchesQuery(node));
    const alpha = dim ? 0.22 : 1;
    const color = KIND_COLORS[node.kind] || KIND_COLORS.unknown;

    ctx.save();
    ctx.globalAlpha = alpha;

    // halo for selected / hovered
    if (node === state.selected || node === state.hovered) {
      ctx.beginPath();
      ctx.arc(node._x, node._y, node._radius + 6, 0, Math.PI * 2);
      ctx.fillStyle = "rgba(56, 189, 248, 0.16)";
      ctx.fill();
    }

    // node body
    ctx.beginPath();
    ctx.arc(node._x, node._y, node._radius, 0, Math.PI * 2);
    ctx.fillStyle = color;
    ctx.fill();
    ctx.lineWidth = 1.4 / state.transform.k;
    ctx.strokeStyle = "rgba(8, 13, 26, 0.75)";
    ctx.stroke();

    // route / orm rings
    if (node._route) {
      ctx.beginPath();
      ctx.arc(node._x, node._y, node._radius + 2.6, 0, Math.PI * 2);
      ctx.strokeStyle = COLOR.route;
      ctx.lineWidth = 1.6 / state.transform.k;
      ctx.stroke();
    }
    if (node._orm) {
      ctx.beginPath();
      ctx.arc(node._x, node._y, node._radius + (node._route ? 5 : 2.6), 0, Math.PI * 2);
      ctx.strokeStyle = COLOR.orm;
      ctx.lineWidth = 1.4 / state.transform.k;
      ctx.setLineDash([3 / state.transform.k, 3 / state.transform.k]);
      ctx.stroke();
      ctx.setLineDash([]);
    }
    if (node.is_dead_candidate) {
      ctx.beginPath();
      ctx.arc(node._x, node._y, node._radius - 2, 0, Math.PI * 2);
      ctx.strokeStyle = "rgba(248, 113, 113, 0.75)";
      ctx.lineWidth = 1 / state.transform.k;
      ctx.stroke();
    }

    // labels
    const showLabel =
      state.transform.k > 0.62 ||
      node === state.selected ||
      node === state.hovered ||
      node._route ||
      node._orm;
    if (showLabel && !dim) {
      const fontSize = Math.max(9.5, 11 / state.transform.k);
      ctx.font = `${fontSize}px "JetBrains Mono", Consolas, monospace`;
      ctx.textAlign = "center";
      ctx.textBaseline = "top";
      const text = node.name;
      const y = node._y + node._radius + 4 / state.transform.k;
      const metrics = ctx.measureText(text);
      if (metrics.width > 150 / state.transform.k) {
        const trimmed = text.slice(0, 22) + "…";
        ctx.fillStyle = "rgba(8, 13, 26, 0.72)";
        ctx.fillRect(
          node._x - metrics.width / 2 - 2,
          y - 1,
          metrics.width + 4,
          fontSize + 2
        );
        ctx.fillStyle = node === state.selected ? "#ffffff" : COLOR.label;
        ctx.fillText(trimmed, node._x, y);
      } else {
        ctx.fillStyle = "rgba(8, 13, 26, 0.72)";
        ctx.fillRect(
          node._x - metrics.width / 2 - 2,
          y - 1,
          metrics.width + 4,
          fontSize + 2
        );
        ctx.fillStyle = node === state.selected ? "#ffffff" : COLOR.label;
        ctx.fillText(text, node._x, y);
      }
    }

    ctx.restore();
  }
}

// -------------------------------------------------------------- tooltip

function drawTooltip() {
  const node = state.hovered;
  if (!node) {
    tooltip.classList.remove("visible");
    return;
  }
  const parts = [
    `<div class="t-name">${escapeHtml(node.name)}</div>`,
    `<div class="t-meta">${escapeHtml(node.module || "")} · ${KIND_LABELS[node.kind] || node.kind}</div>`,
  ];
  if (node._route) {
    parts.push(
      `<div class="t-route">${escapeHtml(node._route.methods.join("/"))} ${escapeHtml(node._route.path)}</div>`
    );
  }
  tooltip.innerHTML = parts.join("");
  tooltip.classList.add("visible");
  const x = Math.min(window.innerWidth - tooltip.offsetWidth - 12, state.pointer.x + 14);
  const y = Math.min(window.innerHeight - tooltip.offsetHeight - 12, state.pointer.y + 14);
  tooltip.style.left = `${x}px`;
  tooltip.style.top = `${y}px`;
}

function escapeHtml(text) {
  return String(text)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

// ---------------------------------------------------------- interactions

function screenToWorld(clientX, clientY) {
  const rect = canvas.getBoundingClientRect();
  const x = clientX - rect.left;
  const y = clientY - rect.top;
  return {
    x: (x - state.transform.x) / state.transform.k,
    y: (y - state.transform.y) / state.transform.k,
  };
}

function pickNode(clientX, clientY) {
  const world = screenToWorld(clientX, clientY);
  let best = null;
  let bestDist = Infinity;
  for (const node of state.nodes) {
    if (!isVisible(node)) continue;
    const dist = Math.hypot(node._x - world.x, node._y - world.y);
    const hit = node._radius + 7 / state.transform.k;
    if (dist <= hit && dist < bestDist) {
      best = node;
      bestDist = dist;
    }
  }
  return best;
}

canvas.addEventListener("pointerdown", (event) => {
  canvas.setPointerCapture(event.pointerId);
  const node = pickNode(event.clientX, event.clientY);
  if (node) {
    state.dragging = {
      node,
      offsetX: node._x - screenToWorld(event.clientX, event.clientY).x,
      offsetY: node._y - screenToWorld(event.clientX, event.clientY).y,
      moved: false,
    };
    selectNode(node);
  } else {
    state.panning = {
      startX: event.clientX,
      startY: event.clientY,
      originX: state.transform.x,
      originY: state.transform.y,
    };
    canvas.classList.add("dragging");
  }
});

canvas.addEventListener("pointermove", (event) => {
  state.pointer.x = event.clientX;
  state.pointer.y = event.clientY;

  if (state.dragging) {
    const world = screenToWorld(event.clientX, event.clientY);
    state.dragging.node._x = world.x + state.dragging.offsetX;
    state.dragging.node._y = world.y + state.dragging.offsetY;
    state.dragging.node._vx = 0;
    state.dragging.node._vy = 0;
    state.dragging.moved = true;
    state.alpha = Math.max(state.alpha, 0.25);
  } else if (state.panning) {
    state.transform.x = state.panning.originX + (event.clientX - state.panning.startX);
    state.transform.y = state.panning.originY + (event.clientY - state.panning.startY);
  } else {
    state.hovered = pickNode(event.clientX, event.clientY);
    canvas.style.cursor = state.hovered ? "pointer" : "grab";
  }
});

canvas.addEventListener("pointerup", (event) => {
  if (state.dragging && !state.dragging.moved) {
    selectNode(state.dragging.node);
  }
  state.dragging = null;
  state.panning = null;
  canvas.classList.remove("dragging");
  try {
    canvas.releasePointerCapture(event.pointerId);
  } catch (_) {
    /* pointer already released */
  }
});

canvas.addEventListener("pointerleave", () => {
  state.hovered = null;
});

canvas.addEventListener("wheel", (event) => {
  event.preventDefault();
  const rect = canvas.getBoundingClientRect();
  const px = event.clientX - rect.left;
  const py = event.clientY - rect.top;
  const factor = Math.exp(-event.deltaY * 0.0016);
  const next = clamp(state.transform.k * factor, 0.12, 4.5);
  const ratio = next / state.transform.k;
  state.transform.x = px - (px - state.transform.x) * ratio;
  state.transform.y = py - (py - state.transform.y) * ratio;
  state.transform.k = next;
}, { passive: false });

canvas.addEventListener("dblclick", (event) => {
  const node = pickNode(event.clientX, event.clientY);
  if (node) focusNode(node);
});

function clamp(value, min, max) {
  return Math.min(max, Math.max(min, value));
}

// ------------------------------------------------------------- selection

async function selectNode(node) {
  state.selected = node;
  document.getElementById("empty-state").hidden = true;
  document.getElementById("details").hidden = false;

  const kind = node.kind || "unknown";
  const badge = document.getElementById("detail-kind");
  badge.textContent = KIND_LABELS[kind] || kind;
  badge.style.color = KIND_COLORS[kind] + "";
  document.getElementById("detail-name").textContent = node.name || node.id;
  document.getElementById("detail-id").textContent = node.id;

  // badges
  const badges = [];
  if (node._route) {
    badges.push(
      `<span class="badge badge-route">${escapeHtml(node._route.methods.join("/"))} ${escapeHtml(node._route.path)}</span>`
    );
  }
  if (node._orm) badges.push('<span class="badge badge-orm">ORM 模型</span>');
  if (node.is_entry) badges.push('<span class="badge badge-entry">入口点</span>');
  if (node.is_dead_candidate) badges.push('<span class="badge badge-dead">死代码候选</span>');
  if (node.is_async) badges.push('<span class="badge">async</span>');
  if (node.is_module_facade) badges.push('<span class="badge">包门面</span>');
  document.getElementById("detail-badges").innerHTML = badges.join("");

  // metrics
  const metrics = [
    ["PageRank", (node.pagerank || 0).toFixed(4)],
    ["入度", String(node.in_degree ?? "—")],
    ["出度", String(node.out_degree ?? "—")],
    ["复杂度", String(node.complexity ?? 0)],
  ];
  document.getElementById("detail-metrics").innerHTML = metrics
    .map(
      ([label, value]) =>
        `<div class="metric"><span class="label">${label}</span><span class="value">${escapeHtml(value)}</span></div>`
    )
    .join("");

  // docs
  document.getElementById("detail-doc").textContent =
    node.docstring || "（无文档字符串）";
  const signature = document.getElementById("detail-signature");
  if (node.signature) {
    signature.textContent = node.signature;
    signature.hidden = false;
  } else {
    signature.hidden = true;
  }
  document.getElementById("source-loc").textContent = node.file
    ? `${node.file}:${node.lineno}`
    : "";

  renderEdgeLists(node);
  renderSource(node);
}

function edgeItem(edge, direction, node) {
  const otherId = direction === "out" ? edge.target : edge.source;
  const other = state.byId.get(otherId);
  const label = EDGE_LABELS[edge.kind] || edge.kind;
  const arrow = direction === "out" ? "→" : "←";
  const classes = [
    edge.inferred ? "inferred" : "",
    edge.kind === "data_flow" ? "dataflow" : "",
  ]
    .filter(Boolean)
    .join(" ");
  const confidence = edge.inferred
    ? ` · ${edge.confidence || "medium"}`
    : "";
  const reason =
    edge.inferred && edge.reason
      ? `<span class="why">${escapeHtml(edge.reason)}</span>`
      : "";
  const name = other ? other.name || other.id : otherId;
  return (
    `<li class="${classes}" data-node="${escapeHtml(otherId)}">` +
    `<span class="rel">${label}${confidence}</span>` +
    `<span class="target">${arrow} ${escapeHtml(name)}<code style="opacity:.55;display:block;font-size:9.5px">${escapeHtml(otherId)}</code>${reason}</span>` +
    `</li>`
  );
}

function renderEdgeLists(node) {
  const adjacency = state.adjacency.get(node.id) || { out: [], in: [] };
  const inferred = [...adjacency.out, ...adjacency.in].filter((e) => e.inferred);
  const inferenceBlock = document.getElementById("block-inference");
  if (inferred.length) {
    inferenceBlock.hidden = false;
    document.getElementById("detail-inference").innerHTML = inferred
      .slice(0, 8)
      .map((edge) =>
        edgeItem(edge, edge.source === node.id ? "out" : "in", node)
      )
      .join("");
  } else {
    inferenceBlock.hidden = true;
  }

  const rows = [];
  for (const edge of adjacency.out.slice(0, 10)) rows.push(edgeItem(edge, "out", node));
  for (const edge of adjacency.in.slice(0, 10)) rows.push(edgeItem(edge, "in", node));
  document.getElementById("detail-edges").innerHTML = rows.length
    ? rows.join("")
    : '<li class="empty-row">没有已解析的调用/依赖关系（悬空调用会进入 AI 推断池）</li>';

  for (const item of document.querySelectorAll("#detail-edges li[data-node], #detail-inference li[data-node]")) {
    item.addEventListener("click", () => {
      const target = state.byId.get(item.dataset.node);
      if (target) {
        selectNode(target);
        focusNode(target);
      }
    });
  }
}

async function renderSource(node) {
  const pre = document.getElementById("detail-source");
  pre.textContent = "加载中…";
  if (!node.file) {
    pre.textContent = "（该节点没有源文件信息）";
    return;
  }
  const start = Math.max(1, (node.lineno || 1) - 2);
  const end = Math.min(
    (node.end_lineno || node.lineno || 1) + 2,
    start + 80
  );
  const key = `${node.file}:${start}:${end}`;
  if (state.sourceCache.has(key)) {
    pre.textContent = state.sourceCache.get(key);
    return;
  }
  try {
    const response = await fetch(
      `/api/source?file=${encodeURIComponent(node.file)}&start=${start}&end=${end}`
    );
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const payload = await response.json();
    const text = (payload.lines || []).join("\n") || "（空文件）";
    state.sourceCache.set(key, text);
    if (state.selected === node) pre.textContent = text;
  } catch (error) {
    if (state.selected === node) {
      pre.textContent = `无法读取源码：${error.message}`;
    }
  }
}

// ---------------------------------------------------------------- camera

function fitToView(animate = true) {
  if (!state.nodes.length) return;
  const rect = canvas.getBoundingClientRect();
  const pad = 70;
  let minX = Infinity;
  let minY = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;
  for (const node of state.nodes) {
    if (!isVisible(node)) continue;
    minX = Math.min(minX, node._x - node._radius);
    minY = Math.min(minY, node._y - node._radius);
    maxX = Math.max(maxX, node._x + node._radius);
    maxY = Math.max(maxY, node._y + node._radius);
  }
  if (!Number.isFinite(minX)) return;
  const width = Math.max(1, maxX - minX);
  const height = Math.max(1, maxY - minY);
  const scale = clamp(
    Math.min((rect.width - pad * 2) / width, (rect.height - pad * 2) / height),
    0.12,
    2.2
  );
  const target = {
    k: scale,
    x: rect.width / 2 - ((minX + maxX) / 2) * scale,
    y: rect.height / 2 - ((minY + maxY) / 2) * scale,
  };
  if (animate) {
    animateTransform(target);
  } else {
    Object.assign(state.transform, target);
  }
}

function animateTransform(target) {
  const from = { ...state.transform };
  const started = performance.now();
  const duration = 320;
  const step = (now) => {
    const t = clamp((now - started) / duration, 0, 1);
    const eased = 1 - Math.pow(1 - t, 3);
    state.transform.k = from.k + (target.k - from.k) * eased;
    state.transform.x = from.x + (target.x - from.x) * eased;
    state.transform.y = from.y + (target.y - from.y) * eased;
    if (t < 1) requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
}

function focusNode(node, instant = false) {
  const rect = canvas.getBoundingClientRect();
  const scale = Math.max(state.transform.k, 0.75);
  const target = {
    k: scale,
    x: rect.width / 2 - node._x * scale,
    y: rect.height / 2 - node._y * scale,
  };
  if (instant || rect.width === 0) {
    Object.assign(state.transform, target);
  } else {
    animateTransform(target);
  }
}

// ------------------------------------------------------------------- UI

function updateStats() {
  const inferred = state.edges.filter((e) => e.inferred).length;
  const flow = state.edges.filter((e) => e.kind === "data_flow").length;
  document.getElementById("stat-nodes").textContent = state.nodes.length;
  document.getElementById("stat-edges").textContent = state.edges.length;
  document.getElementById("stat-inferred").textContent = inferred;
  document.getElementById("stat-flow").textContent = flow;
}

function buildKindFilters() {
  const counts = new Map();
  for (const node of state.nodes) {
    counts.set(node.kind, (counts.get(node.kind) || 0) + 1);
  }
  const legend = document.getElementById("legend");
  const wrapper = document.createElement("div");
  wrapper.className = "legend-title";
  wrapper.style.marginTop = "12px";
  wrapper.textContent = "按类型筛选";
  legend.appendChild(wrapper);

  const row = document.createElement("div");
  row.style.display = "flex";
  row.style.flexWrap = "wrap";
  row.style.gap = "4px";
  row.style.maxWidth = "210px";
  for (const [kind, count] of [...counts.entries()].sort((a, b) => b[1] - a[1])) {
    const chip = document.createElement("button");
    chip.className = "chip";
    chip.style.cursor = "pointer";
    chip.style.color = KIND_COLORS[kind] || "#94a3b8";
    chip.style.borderColor = "rgba(148,163,184,.25)";
    chip.textContent = `${kind} ${count}`;
    chip.dataset.kind = kind;
    chip.addEventListener("click", () => {
      if (state.hiddenKinds.has(kind)) {
        state.hiddenKinds.delete(kind);
        chip.style.opacity = "1";
      } else {
        state.hiddenKinds.add(kind);
        chip.style.opacity = "0.35";
      }
    });
    row.appendChild(chip);
  }
  legend.appendChild(row);

  const dead = document.createElement("label");
  dead.style.display = "flex";
  dead.style.alignItems = "center";
  dead.style.gap = "6px";
  dead.style.marginTop = "10px";
  dead.style.cursor = "pointer";
  dead.innerHTML =
    '<input type="checkbox" id="toggle-dead" style="accent-color:#38bdf8" /> 隐藏死代码候选';
  legend.appendChild(dead);
  dead.querySelector("input").addEventListener("change", (event) => {
    state.hideDead = event.target.checked;
  });
}

document.getElementById("search").addEventListener("input", (event) => {
  state.query = event.target.value.trim().toLowerCase();
});

document.getElementById("search").addEventListener("keydown", (event) => {
  if (event.key === "Enter") {
    const match = state.nodes.find((node) => matchesQuery(node) && isVisible(node));
    if (match) {
      selectNode(match);
      focusNode(match);
    }
  }
});

document.getElementById("btn-fit").addEventListener("click", () => fitToView(true));
document.getElementById("btn-relayout").addEventListener("click", () => {
  const golden = Math.PI * (3 - Math.sqrt(5));
  state.nodes.forEach((node, index) => {
    const angle = index * golden + pseudo(index);
    const ring = 40 + Math.sqrt(index + 1) * 34;
    node._x = Math.cos(angle) * ring;
    node._y = Math.sin(angle) * ring;
    node._vx = 0;
    node._vy = 0;
  });
  state.alpha = 1;
  state.needsFit = true;
  startSimulation();
});

document.getElementById("btn-png").addEventListener("click", exportPng);

document.getElementById("btn-close").addEventListener("click", () => {
  state.selected = null;
  document.getElementById("details").hidden = true;
  document.getElementById("empty-state").hidden = false;
});

window.addEventListener("keydown", (event) => {
  const tag = (event.target.tagName || "").toLowerCase();
  if (tag === "input" || tag === "textarea") {
    if (event.key === "Escape") event.target.blur();
    return;
  }
  if (event.key === "/") {
    event.preventDefault();
    document.getElementById("search").focus();
  } else if (event.key === "f" || event.key === "F") {
    fitToView(true);
  } else if (event.key === "r" || event.key === "R") {
    document.getElementById("btn-relayout").click();
  } else if (event.key === "Escape") {
    document.getElementById("btn-close").click();
  }
});

function exportPng() {
  const rect = canvas.getBoundingClientRect();
  const scale = dpr();
  const out = document.createElement("canvas");
  out.width = Math.floor(rect.width * scale);
  out.height = Math.floor(rect.height * scale);
  const outCtx = out.getContext("2d");
  outCtx.fillStyle = "#080d1a";
  outCtx.fillRect(0, 0, out.width, out.height);
  outCtx.drawImage(canvas, 0, 0);

  const link = document.createElement("a");
  link.download = "moray-scribe-topology.png";
  link.href = out.toDataURL("image/png");
  link.click();
}

window.addEventListener("resize", resize);
boot();
