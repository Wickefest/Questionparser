import { AssetImage, DataTable, MathHtml, RichHtml } from "../components/Rich";
import { upgradeNumberedLists } from "../lib/normalize";

/**
 * Reusable canonical question renderer.
 * Renders content[] in exact stored order. Block type decides rendering.
 */
export function QuestionRenderer({
  content = [],
  options = null,
  assetUrls = {},
  selected = null,
  onSelect = null,
  correct = null,
  revealAnswer = false,
  interactive = false,
  partsAnswer = null,
  activePartLabel = null,
  partAnswers = null,
  onPartAnswerChange = null,
}) {
  const blocks = upgradeNumberedLists(content);
  const structured = options?.mode === "structured";
  const showStemFallback =
    structured &&
    !(blocks || []).some((b) => b?.type === "text" && String(b.value || "").trim()) &&
    String(options?.stem || "").trim();

  return (
    <div className="question-renderer">
      {showStemFallback ? (
        <section className="content-block">
          <p className="stem-line">
            <RichHtml text={options.stem} />
          </p>
        </section>
      ) : null}
      <ContentBlockRenderer blocks={blocks} assetUrls={assetUrls} />
      <OptionRenderer
        options={options}
        assetUrls={assetUrls}
        selected={selected}
        onSelect={interactive ? onSelect : undefined}
        correct={correct}
        revealAnswer={revealAnswer}
        activePartLabel={activePartLabel}
        interactive={interactive}
        partAnswers={partAnswers}
        onPartAnswerChange={onPartAnswerChange}
      />
      {revealAnswer && partsAnswer?.length ? (
        <StructuredAnswerRenderer
          parts={partsAnswer}
          activePartLabel={activePartLabel}
          assetUrls={assetUrls}
        />
      ) : null}
    </div>
  );
}

export function ContentBlockRenderer({ blocks, assetUrls }) {
  if (!blocks?.length) return null;
  return (
    <div className="content-flow">
      {blocks.map((block, index) => (
        <ContentBlock key={index} block={block} assetUrls={assetUrls} />
      ))}
    </div>
  );
}

function ContentBlock({ block, assetUrls }) {
  if (!block?.type) return null;
  if (block.type === "text") {
    return (
      <section className="content-block">
        <p className="stem-line">
          <RichHtml text={block.value} />
        </p>
      </section>
    );
  }
  if (block.type === "math") {
    return (
      <section className="content-block">
        <div className="math-block">
          <MathHtml latex={block.latex} display />
        </div>
      </section>
    );
  }
  if (block.type === "chemistry") {
    return (
      <section className="content-block">
        <div className="chem-block">
          <MathHtml latex={block.value} chem display />
        </div>
      </section>
    );
  }
  if (block.type === "image") {
    const url = resolveAsset(block, assetUrls);
    return (
      <section className="content-block">
        {url ? (
          <figure className="asset-frame">
            <img src={url} alt="Question visual" className="asset-img" loading="lazy" />
          </figure>
        ) : (
          <div className="asset-missing warn">Visual asset missing</div>
        )}
      </section>
    );
  }
  if (block.type === "table") {
    return (
      <section className="content-block">
        <DataTable columns={block.columns} rows={block.rows} />
      </section>
    );
  }
  if (block.type === "list") {
    const Tag = block.ordered === false ? "ul" : "ol";
    return (
      <section className="content-block">
        <Tag className="content-list">
          {(block.items || []).map((item, i) => (
            <li key={i}>
              <RichHtml text={item} />
            </li>
          ))}
        </Tag>
      </section>
    );
  }
  return (
    <section className="content-block muted">Unsupported block: {block.type}</section>
  );
}

export function OptionRenderer({
  options,
  assetUrls,
  selected,
  onSelect,
  correct,
  revealAnswer,
  activePartLabel = null,
  interactive = false,
  partAnswers = null,
  onPartAnswerChange = null,
}) {
  if (!options) return null;
  const mode = options.mode || "text";

  if (mode === "table") {
    return (
      <section className="content-block">
        <div className="options-table-wrap">
          <DataTable columns={options.columns} rows={options.rows} rowLabels />
        </div>
        <div className="options option-keys">
          {(options.rows || []).map((row) => (
            <OptionButton
              key={row.label}
              label={row.label}
              selected={selected === row.label}
              correct={revealAnswer && correct === row.label}
              wrong={revealAnswer && selected === row.label && selected !== correct}
              onSelect={onSelect}
            >
              <span className="muted">row {row.label}</span>
            </OptionButton>
          ))}
        </div>
      </section>
    );
  }

  if (mode === "composite_visual") {
    const url = resolveAsset(options, assetUrls);
    return (
      <section className="content-block">
        {url ? (
          <figure className="asset-frame options-visual">
            <img src={url} alt="Options A–D" className="asset-img" />
          </figure>
        ) : (
          <div className="asset-missing warn">Composite options visual missing</div>
        )}
        <div className="options option-keys">
          {(options.labels || ["A", "B", "C", "D"]).map((label) => (
            <OptionButton
              key={label}
              label={label}
              selected={selected === label}
              correct={revealAnswer && correct === label}
              wrong={revealAnswer && selected === label && selected !== correct}
              onSelect={onSelect}
            >
              <span className="muted">visual choice</span>
            </OptionButton>
          ))}
        </div>
      </section>
    );
  }

  if (mode === "structured") {
    return (
      <StructuredPartsRenderer
        options={options}
        activePartLabel={activePartLabel}
        assetUrls={assetUrls}
        interactive={interactive}
        partAnswers={partAnswers}
        onPartAnswerChange={onPartAnswerChange}
      />
    );
  }

  if (mode === "math") {
    return (
      <section className="content-block">
        <div className="options">
          {(options.items || []).map((item) => (
            <OptionButton
              key={item.label}
              label={item.label}
              selected={selected === item.label}
              correct={revealAnswer && correct === item.label}
              wrong={revealAnswer && selected === item.label && selected !== correct}
              onSelect={onSelect}
            >
              <MathHtml latex={item.latex} />
            </OptionButton>
          ))}
        </div>
      </section>
    );
  }

  if (mode === "mixed") {
    const url = resolveAsset(options, assetUrls);
    return (
      <section className="content-block">
        {url ? (
          <figure className="asset-frame">
            <img src={url} alt="Mixed options" className="asset-img" />
          </figure>
        ) : null}
        <div className="options">
          {(options.items || []).map((item) => (
            <OptionButton
              key={item.label}
              label={item.label}
              selected={selected === item.label}
              correct={revealAnswer && correct === item.label}
              wrong={revealAnswer && selected === item.label && selected !== correct}
              onSelect={onSelect}
            >
              {item.latex ? (
                <MathHtml latex={item.latex} />
              ) : item.asset_id || item.asset ? (
                <AssetImage
                  asset={item.asset_id || item.asset}
                  assetsBase=""
                  alt={`Option ${item.label}`}
                />
              ) : (
                <RichHtml text={item.content || ""} />
              )}
            </OptionButton>
          ))}
        </div>
      </section>
    );
  }

  return (
    <section className="content-block">
      <div className="options">
        {(options.items || []).map((item) => (
          <OptionButton
            key={item.label}
            label={item.label}
            selected={selected === item.label}
            correct={revealAnswer && correct === item.label}
            wrong={revealAnswer && selected === item.label && selected !== correct}
            onSelect={onSelect}
          >
            {item.requiresVisual ? (
              <span className="muted">&lt;visual&gt;</span>
            ) : (
              <RichHtml text={item.content || "—"} />
            )}
          </OptionButton>
        ))}
      </div>
    </section>
  );
}

function StructuredPartsRenderer({
  options,
  activePartLabel = null,
  assetUrls = {},
  interactive = false,
  partAnswers = null,
  onPartAnswerChange = null,
}) {
  const nav = options?.navigationOrder || [];
  const parts = options?.parts || [];
  const marksTotal = options?.marksTotal ?? options?.marks_total;

  const rawItems =
    nav.length > 0
      ? nav
      : flattenParts(parts);
  const items = rawItems.filter((p) => !isEmptySectionShell(p));

  const visible = activePartLabel
    ? items.filter((p) => p.label === activePartLabel)
    : items;

  if (!visible.length && !String(options?.stem || "").trim()) {
    return (
      <section className="content-block muted">
        No structured parts extracted yet — re-parse this paper or edit manually.
      </section>
    );
  }

  return (
    <section className="content-block structured-parts">
      {marksTotal != null && !activePartLabel ? (
        <div className="tiny muted" style={{ marginBottom: 8 }}>
          Total marks: {marksTotal}
        </div>
      ) : null}
      {visible.map((part, i) => {
        const label = part.label || "?";
        const prompt = part.prompt || "";
        const marks = part.marks;
        const kind = part.responseKind || part.response_kind || "text";
        // A part with children (e.g. "c" leading into "c(i)", "c(ii)") is a
        // lead-in statement, not an answerable part — only leaves collect
        // an answer. Rendering a box on the parent too would let a student
        // "answer" a bare statement that has no mark scheme entry.
        const hasKids = Boolean(part.hasChildren || part.children?.length);
        return (
          <div key={`${label}-${i}`} className="structured-part">
            <p className="stem-line">
              <strong>({label})</strong>{" "}
              {prompt ? <RichHtml text={prompt} /> : <span className="muted">—</span>}
              {marks != null ? <span className="muted"> [{marks}]</span> : null}
            </p>
            {(part.visuals || []).map((vis, vi) => {
              const url = resolveAsset(vis, assetUrls);
              return url ? (
                <figure key={vi} className="asset-frame" style={{ marginTop: 8 }}>
                  <img src={url} alt={`Part ${label} diagram`} className="asset-img" />
                </figure>
              ) : null;
            })}
            {hasKids ? null : interactive ? (
              <textarea
                className="structured-part-answer"
                rows={kind === "diagram" || kind === "structure" || kind === "image" ? 2 : 4}
                placeholder={
                  kind === "diagram" || kind === "structure" || kind === "image"
                    ? "Describe your diagram/structure here (no photo upload yet)…"
                    : "Your answer…"
                }
                value={(partAnswers && partAnswers[label]) || ""}
                onChange={(e) => onPartAnswerChange && onPartAnswerChange(label, e.target.value)}
              />
            ) : (
              <div className="tiny muted" style={{ marginTop: 4 }}>
                Answer type: {kind}
                {kind === "draw" ? " · drawing space" : ""}
                {kind === "calculation" ? " · working space" : ""}
                {kind === "text" ? " · text answer" : ""}
              </div>
            )}
          </div>
        );
      })}
    </section>
  );
}

function flattenParts(parts, parent = "") {
  const out = [];
  for (const part of parts || []) {
    const label = parent ? `${parent}(${part.label})` : part.label;
    const prompt = part.prompt || "";
    const hasKids = Boolean(part.children?.length);
    if (!(hasKids && !String(prompt).trim() && !(part.visuals || []).length)) {
      out.push({
        label,
        prompt,
        marks: part.marks,
        responseKind: part.responseKind || part.response_kind || "text",
        visuals: part.visuals || [],
      });
    }
    if (hasKids) out.push(...flattenParts(part.children, label));
  }
  return out;
}

function isEmptySectionShell(part) {
  if (part?.sectionOnly) return true;
  const prompt = String(part?.prompt || "").trim();
  const hasKids = Boolean(part?.hasChildren || part?.children?.length);
  const hasVisual = Boolean((part?.visuals || []).length);
  return hasKids && !prompt && !hasVisual;
}

function StructuredAnswerRenderer({ parts, activePartLabel = null, assetUrls = {} }) {
  const filtered = activePartLabel
    ? (parts || []).filter((p) => normalizePartLabel(p.label) === normalizePartLabel(activePartLabel))
    : parts || [];
  if (!filtered.length) return null;
  return (
    <section className="content-block structured-answers">
      <div className="label">Mark-scheme answer</div>
      {filtered.map((part, i) => {
        const label = part.label || "?";
        const accepted = part.accepted_answers || part.acceptedAnswers || [];
        const notes = part.notes;
        const marks = part.marks_max ?? part.marksMax;
        const visuals = part.visuals || [];
        return (
          <div key={`${label}-${i}`} className="structured-answer-part">
            <div>
              <strong>({label})</strong>
              {marks != null ? <span className="muted"> [{marks}]</span> : null}
            </div>
            {visuals.map((vis, vi) => {
              const url = resolveAsset(vis, assetUrls);
              return url ? (
                <figure key={vi} className="asset-frame" style={{ marginTop: 8 }}>
                  <img src={url} alt={`Answer ${label} diagram`} className="asset-img" />
                </figure>
              ) : null;
            })}
            {accepted.length ? (
              <ul className="content-list">
                {accepted.map((a, j) => (
                  <li key={j}>
                    <RichHtml text={String(a)} />
                  </li>
                ))}
              </ul>
            ) : visuals.length ? null : (
              <div className="muted">No accepted answers extracted</div>
            )}
            {notes ? (
              <p className="tiny muted">
                <RichHtml text={String(notes)} />
              </p>
            ) : null}
          </div>
        );
      })}
    </section>
  );
}

function normalizePartLabel(label) {
  const raw = String(label || "")
    .toLowerCase()
    .replace(/\s+/g, "");
  const m = raw.match(/^\(?([a-z])\)?\(?([ivxlc]+)\)?$/);
  if (m && m[2]) return `${m[1]}(${m[2]})`;
  const letter = raw.match(/^\(?([a-z])\)?$/);
  if (letter) return letter[1];
  const compact = raw.replace(/[^a-z]/g, "");
  const c = compact.match(/^([a-z])([ivxlc]+)$/);
  if (c) return `${c[1]}(${c[2]})`;
  return compact || raw;
}

function OptionButton({ label, selected, correct, wrong, onSelect, children }) {
  const classes = [
    "option",
    selected ? "selected" : "",
    correct ? "correct" : "",
    wrong ? "wrong" : "",
    onSelect ? "" : "static",
  ]
    .filter(Boolean)
    .join(" ");

  if (!onSelect) {
    return (
      <div className={classes}>
        <span className="key">{label}</span>
        <span className="option-body">{children}</span>
      </div>
    );
  }
  return (
    <button type="button" className={classes} onClick={() => onSelect(label)}>
      <span className="key">{label}</span>
      <span className="option-body">{children}</span>
    </button>
  );
}

function resolveAsset(obj, assetUrls) {
  if (!obj) return null;
  if (obj.asset_id && assetUrls[obj.asset_id]) return assetUrls[obj.asset_id];
  if (obj.url) return obj.url;
  return null;
}
