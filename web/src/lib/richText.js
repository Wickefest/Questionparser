import katex from "katex";
import "katex/contrib/mhchem";

/**
 * Render exam text with:
 * - $...$ / $$...$$ KaTeX math
 * - chemistry via mhchem when wrapped as \ce{...} or block type chemistry
 * - light auto-chem for common formula tokens (H2O, CO2, Cr2O7^2-…)
 */

// Trailing (?![A-Za-z]) — not \b — so charges ending in +/- still match before punctuation.
// Element units can also appear grouped in parentheses with a multiplier, e.g. Ca(OH)2,
// Al2(SO4)3, (NH4)2CO3 — common in ionic/salt formulas — so those render as one formula
// instead of leaving the "(OH)2" part as unstyled plain text.
const ELEMENT_UNIT = "[A-Z][a-z]?\\d*";
const GROUP_UNIT = `\\((?:${ELEMENT_UNIT})+\\)\\d*`;
const FORMULA_BODY = `(?:${ELEMENT_UNIT}|${GROUP_UNIT}){1,10}`;
const FORMULA = new RegExp(
  `(?<![A-Za-z])(${FORMULA_BODY}(?:\\^(?:\\d+[+-]|[+-])|\\d*[+-])?)(?![A-Za-z])`,
  "g",
);

const SUB_TO_ASCII = {
  "₀": "0",
  "₁": "1",
  "₂": "2",
  "₃": "3",
  "₄": "4",
  "₅": "5",
  "₆": "6",
  "₇": "7",
  "₈": "8",
  "₉": "9",
};
const SUP_TO_ASCII = {
  "⁰": "0",
  "¹": "1",
  "²": "2",
  "³": "3",
  "⁴": "4",
  "⁵": "5",
  "⁶": "6",
  "⁷": "7",
  "⁸": "8",
  "⁹": "9",
  "⁺": "+",
  "⁻": "-",
};

function toMhchemToken(token) {
  // Unicode scripts → ASCII + caret charge for mhchem (Cr₂O₇²⁻ → Cr2O7^2-)
  let out = "";
  let charge = "";
  for (const ch of String(token)) {
    if (SUB_TO_ASCII[ch] != null) {
      out += SUB_TO_ASCII[ch];
      continue;
    }
    if (SUP_TO_ASCII[ch] != null) {
      charge += SUP_TO_ASCII[ch];
      continue;
    }
    out += ch;
  }
  if (charge && !out.includes("^")) out += `^${charge}`;
  else if (charge) out += charge;
  // Cr_2O_7^{2-} style left in plain text
  out = out
    .replace(/_\{([^{}]+)\}/g, "$1")
    .replace(/_(\d)/g, "$1")
    .replace(/\^\{([^{}]+)\}/g, "^$1")
    .replace(/\\\^/g, "^");
  return out;
}

function looksLikeFormula(token) {
  const mhchem = toMhchemToken(token);
  const body = mhchem.replace(/\^.*$/, "").replace(/\d*[+-]$/, "");
  if (!body) return false;
  if (token.length > 28) return false;
  // Avoid ordinary capitalized words and Roman oxidation states (II, VI, …)
  if (/^[A-Z][a-z]+$/.test(body) && !/[\^0-9₊⁺⁻+-]/.test(mhchem)) return false;
  if (/^[IVXLCDM]+$/i.test(body)) return false;
  // Accept: multi-element / digit stoichiometry / explicit ionic charge (Fe^2+, H+)
  if (/[\^]|[0-9].*[+-]|[+-]$/.test(mhchem)) return /^[A-Z]/.test(body);
  // A parenthesised group (Ca(OH)2, Al2(SO4)3, …) is always a genuine formula.
  if (/\(/.test(body)) return true;
  return /[A-Z][a-z]?\d|[A-Z]{2}|[A-Z][a-z]?[A-Z]/.test(body);
}

/** Flatten unicode / light LaTeX chem fragments so FORMULA can see ASCII tokens. */
function preprocessChemText(text) {
  let value = String(text ?? "");
  // Cr_2O_7^{2-} / Cr_2O_7\^{2-} in prose (not already inside $...$)
  value = value
    .replace(/\\\^/g, "^")
    .replace(/([A-Z][a-z]?)_\{([^{}]+)\}/g, "$1$2")
    .replace(/([A-Z][a-z]?)_(\d)/g, "$1$2")
    .replace(/\^\{([^{}]+)\}/g, "^$1");
  // Cr₂O₇²⁻ → Cr2O7^2-
  value = value.replace(
    /(?:[A-Z][a-z]?[0-9₀-₉]*)+(?:[⁰¹²³⁴⁵⁶⁷⁸⁹]*[⁺⁻])?/g,
    (tok) => (/[₀-₉⁰-⁹⁺⁻]/.test(tok) ? toMhchemToken(tok) : tok),
  );
  return value;
}

export function renderLatex(latex, { displayMode = false, chem = false } = {}) {
  const src = chem && !String(latex).includes("\\ce{") ? `\\ce{${latex}}` : latex;
  try {
    return katex.renderToString(src, {
      throwOnError: false,
      displayMode,
      strict: "ignore",
      trust: true,
    });
  } catch {
    return escapeHtml(String(latex ?? ""));
  }
}

export function renderRichText(text) {
  const value = String(text ?? "");
  if (!value) return "";

  // Preserve explicit sub/sup from older extracts
  if (/<\/?(?:sub|sup)>/i.test(value)) {
    return value
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/&lt;(\/?(?:sub|sup))&gt;/gi, "<$1>");
  }

  // Split on $...$ / $$...$$
  const parts = value.split(/(\$\$[\s\S]+?\$\$|\$[^$\n]+?\$)/g);
  return parts
    .map((part) => {
      if (part.startsWith("$$") && part.endsWith("$$")) {
        return renderLatex(part.slice(2, -2), { displayMode: true });
      }
      if (part.startsWith("$") && part.endsWith("$") && part.length > 2) {
        return renderLatex(part.slice(1, -1), { displayMode: false });
      }
      return autoChemHtml(part);
    })
    .join("");
}

function autoChemHtml(text) {
  const prepared = preprocessChemText(text);
  const escaped = escapeHtml(prepared);
  return escaped.replace(FORMULA, (token) => {
    if (!looksLikeFormula(token)) return token;
    return renderLatex(toMhchemToken(token), { chem: true });
  });
}

export function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}
