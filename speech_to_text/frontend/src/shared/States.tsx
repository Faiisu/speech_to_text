import type { ReactNode } from 'react'

export function ApiError({ message, retry }: { message: string; retry: () => void }) {
  return <div className="api-error" role="alert"><span className="error-mark">!</span><div><strong>Backend request failed</strong><p>{message}</p><small>Check the backend status and logs, then try again.</small></div><button className="button button-outline" onClick={retry}>Retry ↻</button></div>
}

export function Loading({ label = 'Loading data from the backend…' }: { label?: string }) {
  return <div className="loading"><span className="spinner" />{label}</div>
}

export function EmptyState({ title, children, action }: { title: string; children: ReactNode; action?: ReactNode }) {
  return <div className="empty-state"><div className="empty-icon">⌁</div><strong>{title}</strong><p>{children}</p>{action}</div>
}
