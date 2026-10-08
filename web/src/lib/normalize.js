/** Normalize smoke / legacy parse JSON into a viewer-friendly shape. */

export function normalizePaper(data, label = "loaded paper", assetsBase = "") {
  if (!data || !Array.isArray(data.questions)) {
    throw new Error("JSON must include a questions array.");
  }

  const questions = data.questions.map((q, index) => normalizeQuestion(q, index));
  return {
    title: data.title || null,
    label,
    assetsBase: assetsBase.replace(/\\/g, "/").replace(/\/?$/, "/"),
    validation: data.validation || null,
    questions,
  };
}

function normalizeQuestion(raw, index) {
  const ref =
    raw.question_ref ??
    raw.questionRef ??
    raw.questionNumber ??
    raw.question_number ??
    String(index + 1);

  const status =
    raw.status ||
    raw.validation?.status ||
    (raw.requires_review || raw.requiresReview ? "NEEDS_REVIEW" : "DRAFT");

  return {
    ref: String(ref),
    questionType: raw.question_type ?? raw.questionType ?? "mcq",
    status,
    requiresReview: Boolean(raw.requires_review ?? raw.requiresReview),
    sourcePage: raw.source_page ?? raw.sourcePage ?? raw.source?.pageStart ?? raw.source?.page_start,
    content: normalizeContent(raw),
    options: normalizeOptions(raw.options),
    answer: normalizeAnswer(raw.answer),
    issues: raw.validation?.issues || [],
  };
}

function normalizeContent(raw) {
  const blocks = raw.content || [];
  if (blocks.length) {
    const mapped = blocks.map((block) => {
      const type = block.type || "text";
      if (type === "text") {
        return { type: "text", value: block.value ?? block.text ?? "" };
      }
      if (type === "math") {
        return { type: "math", latex: block.latex ?? block.value ?? "" };
      }
      if (type === "chemistry") {
        return { type: "chemistry", value: block.value ?? "" };
      }
      if (type === "image" || type === "diagram") {
        return {
          type: "image",
          asset: block.asset ?? block.assetPath ?? block.asset_path ?? "",
          bbox: block.bbox ?? null,
        };
      }
      if (type === "table") {
        return {
          type: "table",
          columns: block.columns ?? block.headers ?? [],
          rows: block.rows ?? [],
        };
      }
      if (type === "list") {
        return {
          type: "list",
          ordered: block.ordered !== false,
          items: block.items ?? [],
        };
      }
      return { type: "text", value: JSON.stringify(block) };
    });
    return upgradeNumberedLists(mapped);
  }

  // Legacy stem-only papers
  if (raw.stem) {
    return upgradeNumberedLists([{ type: "text", value: raw.stem }]);
  }
  return [];
}

/** Split flat "1. / 2. / 3." statement text into ordered list blocks. */
export function upgradeNumberedLists(blocks) {
  if (!Array.isArray(blocks)) return blocks;
  const out = [];
  for (const block of blocks) {
    if (!block || block.type !== "text" || typeof block.value !== "string") {
      out.push(block);
      continue;
    }
    const split = splitTextIntoListBlocks(block.value);
    if (!split) out.push(block);
    else out.push(...split);
  }
  return out;
}

function splitTextIntoListBlocks(rawValue) {
  let text = String(rawValue || "").replace(/\r\n/g, "\n").trim();
  if (text.includes("\\n") && !text.includes("\n")) text = text.replace(/\\n/g, "\n");
  if (!text) return null;
  return splitMultilineList(text) || splitInlineList(text);
}

function splitMultilineList(text) {
  if (!text.includes("\n")) return null;
  const numRe = /^\s*(\d{1,2})\s*[.\)\-:]\s+(\S.*\S|\S)\s*$/;
  const bulletRe = /^\s*[•\-\*]\s+(\S.*\S|\S)\s*$/;
  const lines = text.split("\n");
  const numbered = [];
  const bullets = [];
  lines.forEach((line, idx) => {
    const m = line.match(numRe);
    if (m) {
      numbered.push({ idx, n: Number(m[1]), body: m[2].trim() });
      return;
    }
    const b = line.match(bulletRe);
    if (b) bullets.push({ idx, body: b[1].trim() });
  });

  let ordered = false;
  let items = [];
  let first = -1;
  let last = -1;
  if (numbered.length >= 2) {
    const nums = numbered.map((x) => x.n);
    const expected = Array.from({ length: nums.length }, (_, i) => nums[0] + i);
    if (nums.every((n, i) => n === expected[i])) {
      ordered = true;
      items = numbered.map((x) => x.body);
      first = numbered[0].idx;
      last = numbered[numbered.length - 1].idx;
    }
  }
  if (!items.length && bullets.length >= 2) {
    items = bullets.map((x) => x.body);
    first = bullets[0].idx;
    last = bullets[bullets.length - 1].idx;
  }
  if (items.length < 2) return null;

  for (let i = first; i <= last; i += 1) {
    const line = lines[i].trim();
    if (!line) continue;
    if (ordered && !numRe.test(lines[i])) return null;
    if (!ordered && !bulletRe.test(lines[i])) return null;
  }

  const lead = lines.slice(0, first).join("\n").trim();
  const trail = lines.slice(last + 1).join("\n").trim();
  const out = [];
  if (lead) out.push({ type: "text", value: lead });
  out.push({ type: "list", ordered, items });
  if (trail) out.push({ type: "text", value: trail });
  return out;
}

function splitInlineList(text) {
  const inlineRe = /(?<!\d\.)\b(\d{1,2})(?:[.\)\-:]\s+|\s+)(?=[A-Za-z(])/g;
  const matches = [...text.matchAll(inlineRe)].filter((m) => Number(m[1]) >= 1);
  if (matches.length < 2) return null;

  const isValidStart = (m) => {
    if (m.index === 0) return true;
    const before = text.slice(0, m.index).replace(/\s+$/g, "");
    return before.length > 0 && ".!?".includes(before[before.length - 1]);
  };

  let best = null;
  let i = 0;
  while (i < matches.length) {
    if (Number(matches[i][1]) !== 1 || !isValidStart(matches[i])) {
      i += 1;
      continue;
    }
    const run = [matches[i]];
    let j = i + 1;
    while (j < matches.length) {
      const next = Number(matches[j][1]);
      const expected = Number(run[run.length - 1][1]) + 1;
      if (next === expected) {
        run.push(matches[j]);
        j += 1;
      } else if (next < expected) {
        j += 1; // skip noise like "0" in "from 0 to -1"
      } else {
        break;
      }
    }
    if (run.length >= 2 && (!best || run.length > best.length)) best = run;
    i = Math.max(j, i + 1);
  }
  if (!best) return null;

  const lead = text.slice(0, best[0].index).trim();
  const items = [];
  for (let idx = 0; idx < best.length; idx += 1) {
    const start = best[idx].index + best[idx][0].length;
    const end = idx + 1 < best.length ? best[idx + 1].index : text.length;
    items.push(text.slice(start, end).trim());
  }

  let trail = "";
  if (items.length) {
    const last = items[items.length - 1];
    const tq = /(?<=[.!?])\s+(?=(?:Which|What|How|Identify|Select|Choose|Name|Give|State)\b)/.exec(last);
    if (tq) {
      items[items.length - 1] = last.slice(0, tq.index).trim();
      trail = last.slice(tq.index + tq[0].length).trim();
    } else {
      const loose = /\s+(?=(?:Which|What|How|Identify|Select|Choose)\b)/.exec(last);
      if (loose && loose.index > 6) {
        items[items.length - 1] = last.slice(0, loose.index).trim();
        trail = last.slice(loose.index + loose[0].length).trim();
      }
    }
  }

  const cleaned = items.map((it) => it.replace(/[ ;]+$/g, "")).filter(Boolean);
  if (cleaned.length < 2) return null;
  if (cleaned.some((it) => it.length < 6)) return null;
  if (["and", "or", "to", "of", "in"].includes(cleaned[0].toLowerCase())) return null;

  const out = [];
  if (lead) out.push({ type: "text", value: lead });
  out.push({ type: "list", ordered: true, items: cleaned });
  if (trail) out.push({ type: "text", value: trail });
  return out;
}

function normalizeOptions(options) {
  if (!options) return { mode: "text", items: [] };

  // Canonical discriminated modes
  if (options.mode) {
    if (options.mode === "text") {
      return {
        mode: "text",
        items: (options.items || []).map((item) => ({
          label: item.label,
          content: item.content ?? item.text ?? "",
        })),
      };
    }
    if (options.mode === "math") {
      return {
        mode: "math",
        items: (options.items || []).map((item) => ({
          label: item.label,
          latex: item.latex ?? "",
        })),
      };
    }
    if (options.mode === "table") {
      return {
        mode: "table",
        columns: options.columns || [],
        rows: (options.rows || []).map((row) => ({
          label: row.label,
          cells: row.cells || [],
        })),
      };
    }
    if (options.mode === "composite_visual") {
      return {
        mode: "composite_visual",
        labels: options.labels || ["A", "B", "C", "D"],
        asset: options.asset || "",
      };
    }
    if (options.mode === "mixed") {
      return {
        mode: "mixed",
        items: options.items || [],
        asset: options.asset || null,
      };
    }
  }

  // Legacy A/B/C/D map
  const labels = ["A", "B", "C", "D"];
  if (labels.some((label) => options[label])) {
    return {
      mode: "text",
      items: labels.map((label) => ({
        label,
        content: options[label]?.text ?? options[label]?.content ?? "",
        requiresVisual: Boolean(options[label]?.requiresVisual || options[label]?.requires_visual),
      })),
    };
  }

  return { mode: "text", items: [] };
}

function normalizeAnswer(answer) {
  if (!answer) return null;
  if (typeof answer === "string") {
    return { value: answer, explanation: null, explanationSource: null };
  }
  return {
    value: answer.value ?? answer.correct_option ?? answer.correctOption ?? null,
    explanation: answer.explanation ?? null,
    explanationSource: answer.explanation_source ?? answer.explanationSource ?? null,
  };
}

export function resolveAssetUrl(asset, assetsBase) {
  if (!asset) return null;
  const normalized = String(asset).replace(/\\/g, "/");
  if (/^https?:\/\//i.test(normalized) || normalized.startsWith("blob:")) {
    return normalized;
  }
  if (normalized.startsWith("/")) return normalized;
  if (normalized.startsWith("output/")) return `/${normalized}`;

  const base = (assetsBase || "").replace(/\\/g, "/");
  if (base) {
    const trimmed = base.endsWith("/") ? base : `${base}/`;
    // asset may already include "assets/"
    if (normalized.includes("/")) {
      // If absolute-ish relative path from run dir
      if (normalized.startsWith("assets/")) {
        const runRoot = trimmed.replace(/assets\/?$/, "");
        return `${runRoot}${normalized}`.replace(/\/{2,}/g, "/").replace(":/", "://");
      }
    }
    return `${trimmed}${normalized}`.replace(/([^:]\/)\/+/g, "$1");
  }
  return `/${normalized}`;
}

export function inferAssetsBase(label) {
  const path = String(label || "").replace(/\\/g, "/");
  // /output/smoke-page-16/matched_questions.json → /output/smoke-page-16/assets/
  const match = path.match(/^(.*\/output\/[^/]+)\//);
  if (match) return `${match[1]}/assets/`;
  const bare = path.match(/output\/([^/]+)\//);
  if (bare) return `/output/${bare[1]}/assets/`;
  return "";
}

export const SAMPLE_PAPERS = [
  {
    label: "Smoke page 16 (Q39–40)",
    url: "/output/smoke-page-16/matched_questions.json",
    assetsBase: "/output/smoke-page-16/assets/",
  },
  {
    label: "Smoke page 1 (Q1–2)",
    url: "/output/smoke-1page/matched_questions.json",
    assetsBase: "/output/smoke-1page/assets/",
  },
];
