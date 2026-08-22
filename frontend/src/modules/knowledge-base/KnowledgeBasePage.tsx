import { useMutation, useQuery } from "@tanstack/react-query";
import { type ChangeEvent, useRef, useState } from "react";

import { useAuth } from "@/modules/auth/useAuth";
import { type KnowledgeDocumentDetail, knowledgeApi } from "@/modules/knowledge-base/api";
import { problemMessage } from "@/shared/api/client";
import { queryClient } from "@/shared/api/queryClient";
import { usePagination } from "@/shared/hooks/usePagination";
import { toast } from "@/shared/store/uiStore";
import { Badge } from "@/shared/ui/Badge";
import { Button } from "@/shared/ui/Button";
import { Card } from "@/shared/ui/Card";
import { EmptyState } from "@/shared/ui/EmptyState";
import { Markdown } from "@/shared/ui/Markdown";
import { PageHeader } from "@/shared/ui/PageHeader";
import { Spinner } from "@/shared/ui/Spinner";

const EDITOR_ROLES = ["sme_reviewer", "admin"];
const CATEGORIES = [
  "application_error", "vpn", "vpn_certificate", "password_reset", "mfa", "account_locked",
  "active_directory", "outlook", "teams", "email_config", "browser", "printer", "wifi",
  "network_drives", "shared_folder", "software_install", "windows_update", "docker",
  "git_access", "vscode", "python_env", "sap", "oracle", "database_connection",
];

const invalidateKb = () => queryClient.invalidateQueries({ queryKey: ["knowledge"] });

/* ---------------- editor (create / edit) ---------------- */
function EditorModal({
  mode,
  initial,
  onClose,
}: {
  mode: "create" | "edit";
  initial?: KnowledgeDocumentDetail;
  onClose: () => void;
}) {
  const [title, setTitle] = useState(initial?.title ?? "");
  const [category, setCategory] = useState(initial?.category ?? "application_error");
  const [body, setBody] = useState(initial?.body ?? "## Problem\n\n## Root Cause\n\n## Steps\n1. ");

  const save = useMutation({
    mutationFn: () =>
      mode === "create"
        ? knowledgeApi.create({ title, category, body })
        : knowledgeApi.edit(initial!.id, { title, body }),
    onSuccess: () => {
      toast.success(mode === "create" ? "Draft article created." : "Article updated (new version).");
      invalidateKb();
      if (initial) queryClient.invalidateQueries({ queryKey: ["kb", initial.id] });
      onClose();
    },
    onError: (e) => toast.error(problemMessage(e)),
  });

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 p-4" onClick={onClose}>
      <div className="flex max-h-[88vh] w-full max-w-4xl flex-col rounded-xl bg-white shadow-xl" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between border-b border-slate-100 px-5 py-3">
          <span className="text-sm font-semibold text-slate-900">
            {mode === "create" ? "New knowledge article" : `Edit — ${initial?.title}`}
          </span>
          <button onClick={onClose} className="rounded-lg px-2 py-1 text-slate-400 hover:bg-slate-100">✕</button>
        </div>
        <div className="grid flex-1 grid-cols-2 gap-4 overflow-hidden p-5">
          <div className="flex flex-col gap-3 overflow-y-auto">
            <label className="text-xs font-medium text-slate-500">Title</label>
            <input value={title} onChange={(e) => setTitle(e.target.value)}
              className="rounded-lg border border-slate-200 px-3 py-2 text-sm focus:border-brand-400 focus:outline-none" />
            <label className="text-xs font-medium text-slate-500">Category</label>
            <select value={category} onChange={(e) => setCategory(e.target.value)} disabled={mode === "edit"}
              className="rounded-lg border border-slate-200 px-3 py-2 text-sm focus:border-brand-400 focus:outline-none disabled:bg-slate-50">
              {CATEGORIES.map((c) => <option key={c} value={c}>{c}</option>)}
            </select>
            <label className="text-xs font-medium text-slate-500">Body (Markdown)</label>
            <textarea value={body} onChange={(e) => setBody(e.target.value)}
              className="min-h-[280px] flex-1 resize-none rounded-lg border border-slate-200 px-3 py-2 font-mono text-xs focus:border-brand-400 focus:outline-none" />
          </div>
          <div className="overflow-y-auto rounded-lg border border-slate-100 bg-slate-50 p-3">
            <div className="mb-2 text-xs font-medium text-slate-400">Live preview</div>
            <Markdown content={body} />
          </div>
        </div>
        <div className="flex justify-end gap-2 border-t border-slate-100 px-5 py-3">
          <Button variant="ghost" size="sm" onClick={onClose}>Cancel</Button>
          <Button size="sm" loading={save.isPending} disabled={!title.trim() || !body.trim()} onClick={() => save.mutate()}>
            {mode === "create" ? "Create draft" : "Save changes"}
          </Button>
        </div>
      </div>
    </div>
  );
}

/* ---------------- viewer (read + role actions) ---------------- */
function ArticleModal({
  id,
  isEditor,
  onClose,
  onEdit,
}: {
  id: string;
  isEditor: boolean;
  onClose: () => void;
  onEdit: (doc: KnowledgeDocumentDetail) => void;
}) {
  const detail = useQuery({ queryKey: ["kb", id], queryFn: () => knowledgeApi.get(id) });
  const versions = useQuery({ queryKey: ["kb", id, "versions"], queryFn: () => knowledgeApi.versions(id), enabled: isEditor });

  const setStatus = useMutation({
    mutationFn: (publish: boolean) => (publish ? knowledgeApi.publish(id) : knowledgeApi.unpublish(id)),
    onSuccess: () => {
      toast.success("Status updated.");
      invalidateKb();
      queryClient.invalidateQueries({ queryKey: ["kb", id] });
    },
    onError: (e) => toast.error(problemMessage(e)),
  });
  const published = detail.data?.doc_status === "published";

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 p-4" onClick={onClose}>
      <div className="flex max-h-[85vh] w-full max-w-3xl flex-col rounded-xl bg-white shadow-xl" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between border-b border-slate-100 px-5 py-3">
          <div>
            <div className="text-sm font-semibold text-slate-900">{detail.data?.title ?? "Article"}</div>
            {detail.data && (
              <div className="mt-0.5 flex items-center gap-2 text-xs text-slate-400">
                <span>{detail.data.category}</span>
                <Badge>{detail.data.doc_status}</Badge>
                <span>v{detail.data.version}</span>
              </div>
            )}
          </div>
          <div className="flex items-center gap-2">
            {isEditor && detail.data && (
              <>
                <Button variant="ghost" size="sm" onClick={() => onEdit(detail.data!)}>Edit</Button>
                <Button variant="ghost" size="sm" loading={setStatus.isPending}
                  onClick={() => setStatus.mutate(!published)}>
                  {published ? "Unpublish" : "Publish"}
                </Button>
              </>
            )}
            <button onClick={onClose} className="rounded-lg px-2 py-1 text-slate-400 hover:bg-slate-100">✕</button>
          </div>
        </div>
        <div className="overflow-y-auto px-5 py-4">
          {detail.isLoading ? (
            <Spinner label="Loading article…" />
          ) : detail.isError || !detail.data ? (
            <EmptyState title="Could not load article" hint="Please try again." />
          ) : (
            <Markdown content={detail.data.body} />
          )}
          {isEditor && versions.data && versions.data.length > 0 && (
            <div className="mt-6 border-t border-slate-100 pt-3">
              <div className="mb-2 text-xs font-semibold text-slate-500">Version history</div>
              <ul className="space-y-1 text-xs text-slate-500">
                {versions.data.map((v) => (
                  <li key={v.version} className="flex items-center gap-2">
                    <Badge>v{v.version}</Badge>
                    <span>{v.doc_status}</span>
                    <span className="text-slate-400">{v.change_summary ?? ""}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

/* ---------------- page ---------------- */
export function KnowledgeBasePage() {
  const pagination = usePagination();
  const { user } = useAuth();
  const isEditor = EDITOR_ROLES.includes(user?.role ?? "");
  const fileRef = useRef<HTMLInputElement>(null);
  const [q, setQ] = useState("");
  const [selected, setSelected] = useState<string | null>(null);
  const [editor, setEditor] = useState<{ mode: "create" | "edit"; doc?: KnowledgeDocumentDetail } | null>(null);

  const query = useQuery({
    queryKey: ["knowledge", pagination.page, q],
    queryFn: () => knowledgeApi.list(pagination.page, q || undefined),
  });

  const upload = useMutation({
    mutationFn: (file: File) => knowledgeApi.upload(file, "application_error"),
    onSuccess: () => { toast.success("Document queued for ingestion."); invalidateKb(); },
    onError: (error) => toast.error(problemMessage(error)),
  });

  const onFile = (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (file) upload.mutate(file);
  };

  return (
    <div>
      <PageHeader
        title="Knowledge Base"
        subtitle={isEditor ? "Create, edit and publish help articles" : "Open an article to see the full guided solution"}
        actions={
          <>
            {isEditor && (
              <Button variant="secondary" onClick={() => setEditor({ mode: "create" })}>New article</Button>
            )}
            <input ref={fileRef} type="file" accept=".pdf,.docx,.txt,.md,.html" hidden onChange={onFile} />
            <Button loading={upload.isPending} onClick={() => fileRef.current?.click()}>Upload document</Button>
          </>
        }
      />
      <Card>
        <div className="mb-3">
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search articles by title…"
            className="w-full rounded-lg border border-slate-200 px-3 py-2 text-sm focus:border-brand-400 focus:outline-none" />
        </div>
        {query.isLoading ? (
          <Spinner label="Loading documents…" />
        ) : query.isError ? (
          <EmptyState title="Knowledge service unavailable" hint="The /kb endpoints respond once deployed." />
        ) : !query.data || query.data.items.length === 0 ? (
          <EmptyState title="No articles found" hint="Try a different search, or create an article." />
        ) : (
          <ul className="divide-y divide-slate-100">
            {query.data.items.map((doc) => (
              <li key={doc.id}>
                <button onClick={() => setSelected(doc.id)}
                  className="flex w-full items-center justify-between py-3 text-left hover:bg-slate-50">
                  <div>
                    <p className="text-sm font-medium text-brand-700">{doc.title}</p>
                    <p className="text-xs text-slate-400">{doc.category} · v{doc.version}</p>
                  </div>
                  <Badge>{doc.doc_status}</Badge>
                </button>
              </li>
            ))}
          </ul>
        )}
      </Card>

      {selected && (
        <ArticleModal
          id={selected}
          isEditor={isEditor}
          onClose={() => setSelected(null)}
          onEdit={(doc) => { setSelected(null); setEditor({ mode: "edit", doc }); }}
        />
      )}
      {editor && (
        <EditorModal mode={editor.mode} initial={editor.doc} onClose={() => setEditor(null)} />
      )}
    </div>
  );
}
