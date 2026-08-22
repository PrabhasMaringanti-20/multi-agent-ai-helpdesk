import type { ReactNode } from "react";

/**
 * Dependency-free Markdown renderer for enterprise AI answers.
 * Supports headings (##/###), checkbox lists (- [ ]), bullet/numbered lists,
 * blockquotes (with Warning/Success styling), inline **bold** and `code`.
 */

function inline(text: string, keyBase: string): ReactNode[] {
  const nodes: ReactNode[] = [];
  // Split on **bold** and `code`, keeping delimiters.
  const parts = text.split(/(\*\*[^*]+\*\*|`[^`]+`)/g);
  parts.forEach((p, i) => {
    if (p.startsWith("**") && p.endsWith("**")) {
      nodes.push(
        <strong key={`${keyBase}-b${i}`} className="font-semibold">
          {p.slice(2, -2)}
        </strong>,
      );
    } else if (p.startsWith("`") && p.endsWith("`")) {
      nodes.push(
        <code key={`${keyBase}-c${i}`} className="rounded bg-slate-100 px-1 py-0.5 text-[0.85em]">
          {p.slice(1, -1)}
        </code>,
      );
    } else if (p) {
      nodes.push(<span key={`${keyBase}-t${i}`}>{p}</span>);
    }
  });
  return nodes;
}

export function Markdown({ content }: { content: string }) {
  const lines = content.split("\n");
  const out: ReactNode[] = [];
  let list: ReactNode[] = [];
  let ordered = false;
  const flush = (key: string) => {
    if (list.length === 0) return;
    const items = list;
    list = [];
    out.push(
      ordered ? (
        <ol key={key} className="my-1 ml-5 list-decimal space-y-1">
          {items}
        </ol>
      ) : (
        <ul key={key} className="my-1 space-y-1">
          {items}
        </ul>
      ),
    );
  };

  lines.forEach((raw, i) => {
    const line = raw.trimEnd();
    const key = `md-${i}`;
    if (/^#{1,6}\s/.test(line)) {
      flush(`${key}-l`);
      const text = line.replace(/^#{1,6}\s/, "");
      out.push(
        <h4 key={key} className="mb-1 mt-3 text-sm font-bold text-slate-900 first:mt-0">
          {inline(text, key)}
        </h4>,
      );
    } else if (/^>\s?/.test(line)) {
      flush(`${key}-l`);
      const text = line.replace(/^>\s?/, "");
      const warn = /warning/i.test(text);
      const success = /success/i.test(text);
      out.push(
        <blockquote
          key={key}
          className={`my-2 rounded-md border-l-4 px-3 py-2 text-xs ${
            warn
              ? "border-amber-400 bg-amber-50 text-amber-800"
              : success
                ? "border-emerald-400 bg-emerald-50 text-emerald-800"
                : "border-slate-300 bg-slate-50 text-slate-700"
          }`}
        >
          {inline(text, key)}
        </blockquote>,
      );
    } else if (/^\s*-\s\[[ xX]\]\s/.test(line)) {
      const checked = /\[[xX]\]/.test(line);
      const text = line.replace(/^\s*-\s\[[ xX]\]\s/, "");
      ordered = false;
      list.push(
        <li key={key} className="flex items-start gap-2">
          <span className={`mt-0.5 ${checked ? "text-emerald-600" : "text-slate-400"}`}>
            {checked ? "☑" : "☐"}
          </span>
          <span>{inline(text, key)}</span>
        </li>,
      );
    } else if (/^\s*[-*]\s/.test(line)) {
      ordered = false;
      list.push(
        <li key={key} className="ml-5 list-disc">
          {inline(line.replace(/^\s*[-*]\s/, ""), key)}
        </li>,
      );
    } else if (/^\s*\d+\.\s/.test(line)) {
      ordered = true;
      list.push(<li key={key}>{inline(line.replace(/^\s*\d+\.\s/, ""), key)}</li>);
    } else if (line.trim() === "") {
      flush(`${key}-l`);
    } else {
      flush(`${key}-l`);
      out.push(
        <p key={key} className="my-1">
          {inline(line, key)}
        </p>,
      );
    }
  });
  flush("md-final");
  return <div className="text-sm leading-relaxed text-slate-800">{out}</div>;
}
