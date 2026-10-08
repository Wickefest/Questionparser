import { renderLatex, renderRichText } from "../lib/richText";
import { resolveAssetUrl } from "../lib/normalize";

export function RichHtml({ text, className }) {
  return (
    <span
      className={className}
      dangerouslySetInnerHTML={{ __html: renderRichText(text) }}
    />
  );
}

export function MathHtml({ latex, display = false, chem = false, className }) {
  return (
    <span
      className={className}
      dangerouslySetInnerHTML={{
        __html: renderLatex(latex, { displayMode: display, chem }),
      }}
    />
  );
}

export function AssetImage({ asset, assetsBase, alt, className = "" }) {
  const src = resolveAssetUrl(asset, assetsBase);
  if (!src) {
    return <div className="asset-missing muted">image pending</div>;
  }
  return (
    <figure className={`asset-frame ${className}`.trim()}>
      <img src={src} alt={alt || "Exam visual"} loading="lazy" className="asset-img" />
    </figure>
  );
}

export function DataTable({ columns = [], rows = [], rowLabels = false }) {
  const width = Math.max(
    columns.length,
    ...rows.map((row) => (Array.isArray(row) ? row.length : (row.cells || []).length)),
    0
  );
  const headers = [...columns];
  while (headers.length < width) headers.push("");

  return (
    <div className="table-wrap">
      <table>
        {headers.some((h) => String(h).trim()) ? (
          <thead>
            <tr>
              {rowLabels ? <th /> : null}
              {headers.map((header, i) => (
                <th key={i}>
                  <RichHtml text={header || ""} />
                </th>
              ))}
            </tr>
          </thead>
        ) : null}
        <tbody>
          {rows.map((row, ri) => {
            const label = row.label;
            const cells = Array.isArray(row) ? row : row.cells || [];
            const padded = [...cells];
            while (padded.length < width) padded.push("");
            return (
              <tr key={ri}>
                {rowLabels || label ? <th scope="row">{label || ""}</th> : null}
                {padded.slice(0, width).map((cell, ci) => (
                  <td key={ci}>
                    <RichHtml text={cell == null ? "" : String(cell)} />
                  </td>
                ))}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
