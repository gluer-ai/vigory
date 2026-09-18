import { Upload } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { api, ApiError } from '../../lib/api'
import type { Document } from '../../lib/types'
import { DocumentReviewDialog } from './DocumentReviewDialog'

const REFRESH_MS = 3000

const STATUS_COLOR: Record<Document['status'], string> = {
  processing: 'var(--color-status-unknown)',
  proposed: 'var(--color-status-active)',
  committed: 'var(--color-status-inactive)',
  error: 'var(--color-status-destroyed)',
}

function DocumentStatusChip({ status }: { status: Document['status'] }) {
  return (
    <span
      className="inline-flex items-center rounded px-1.5 py-0.5 text-xs font-medium text-[var(--color-text-inverse)]"
      style={{ background: STATUS_COLOR[status] }}
    >
      {status}
    </span>
  )
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

/** Upload documents (pdf/docx/xlsx/txt/md) and browse the resulting
 * extraction status — the file-upload counterpart to IngestDialog's
 * paste-text flow. Extraction runs in the background server-side; this
 * page polls GET /documents so status updates without any push mechanism. */
export function KnowledgeBasePage() {
  const [documents, setDocuments] = useState<Document[]>([])
  const [loadError, setLoadError] = useState('')
  const [uploading, setUploading] = useState(false)
  const [uploadError, setUploadError] = useState('')
  const [reviewDocId, setReviewDocId] = useState<string | null>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    let cancelled = false
    async function refresh() {
      try {
        const result = await api.listDocuments()
        if (!cancelled) {
          setDocuments(result)
          setLoadError('')
        }
      } catch (err) {
        if (!cancelled) {
          setLoadError(err instanceof ApiError ? err.message : 'Failed to reach the backend')
        }
      }
    }
    refresh()
    const id = setInterval(refresh, REFRESH_MS)
    return () => {
      cancelled = true
      clearInterval(id)
    }
  }, [])

  async function handleFileChange(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0]
    if (fileInputRef.current) fileInputRef.current.value = ''
    if (!file) return
    setUploading(true)
    setUploadError('')
    try {
      const doc = await api.uploadDocument(file)
      setDocuments((prev) => [doc, ...prev])
    } catch (err) {
      setUploadError(err instanceof ApiError ? err.message : 'Failed to reach the backend')
    } finally {
      setUploading(false)
    }
  }

  const reviewDoc = documents.find((d) => d.document_id === reviewDocId) ?? null

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <div className="border-b border-[var(--color-border)] px-6 py-4">
        <h1 className="text-base font-semibold text-[var(--color-text-primary)]">Knowledge base</h1>
        <p className="mt-0.5 text-sm text-[var(--color-text-muted)]">
          Upload a PDF, Word, text, Markdown, or Excel file — the extraction agent proposes
          entities and links from its content for you to review and commit.
        </p>
      </div>
      <div className="flex-1 overflow-y-auto p-6">
        <div className="mb-4 flex items-center gap-3">
          <input
            ref={fileInputRef}
            type="file"
            id="document-upload"
            accept=".txt,.md,.pdf,.docx,.xlsx"
            onChange={handleFileChange}
            disabled={uploading}
            className="hidden"
          />
          <label
            htmlFor="document-upload"
            className={`inline-flex cursor-pointer items-center gap-2 rounded-md bg-[var(--color-focus)] px-3 py-1.5 text-sm font-medium text-[var(--color-text-inverse)] hover:brightness-110 ${uploading ? 'pointer-events-none opacity-50' : ''}`}
          >
            <Upload size={14} />
            {uploading ? 'Uploading…' : 'Upload document…'}
          </label>
          {uploadError && (
            <p role="alert" className="text-sm text-[var(--color-status-destroyed)]">
              {uploadError}
            </p>
          )}
        </div>

        {loadError && (
          <p role="alert" className="mb-3 text-sm text-[var(--color-status-destroyed)]">
            {loadError}
          </p>
        )}

        {documents.length === 0 ? (
          <p className="text-sm text-[var(--color-text-muted)]">No documents uploaded yet.</p>
        ) : (
          <table className="w-full border-collapse text-sm">
            <thead>
              <tr className="border-b border-[var(--color-border)] text-left text-[var(--color-text-muted)]">
                <th className="py-2 pe-3 font-medium">File</th>
                <th className="py-2 pe-3 font-medium">Type</th>
                <th className="py-2 pe-3 font-medium">Size</th>
                <th className="py-2 pe-3 font-medium">Uploaded</th>
                <th className="py-2 pe-3 font-medium">Status</th>
                <th className="py-2 pe-3 font-medium" />
              </tr>
            </thead>
            <tbody>
              {documents.map((doc) => (
                <tr key={doc.document_id} className="border-b border-[var(--color-border)] align-top">
                  <td className="py-2 pe-3 text-[var(--color-text-primary)]">{doc.filename}</td>
                  <td className="py-2 pe-3 font-mono text-xs text-[var(--color-text-muted)]">
                    {doc.file_type}
                  </td>
                  <td className="py-2 pe-3 text-xs text-[var(--color-text-muted)]">
                    {formatSize(doc.size_bytes)}
                  </td>
                  <td className="py-2 pe-3 text-xs text-[var(--color-text-muted)]">
                    {new Date(doc.uploaded_at).toLocaleString()}
                  </td>
                  <td className="py-2 pe-3">
                    <DocumentStatusChip status={doc.status} />
                    {doc.status === 'error' && doc.error_message && (
                      <div className="mt-1 text-xs text-[var(--color-status-destroyed)]">
                        {doc.error_message}
                      </div>
                    )}
                  </td>
                  <td className="py-2 pe-3">
                    {doc.status === 'proposed' && (
                      <button
                        type="button"
                        onClick={() => setReviewDocId(doc.document_id)}
                        className="rounded-md border border-[var(--color-border)] px-2.5 py-1 text-xs text-[var(--color-text-primary)] hover:bg-[var(--color-surface-hover)]"
                      >
                        Review
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <DocumentReviewDialog
        open={reviewDocId !== null}
        batchId={reviewDoc?.batch_id ?? null}
        onOpenChange={(open) => !open && setReviewDocId(null)}
        onCommitted={() => {}}
      />
    </div>
  )
}
