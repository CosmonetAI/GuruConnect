import {
  AudioLines,
  BarChart3,
  ClipboardCheck,
  ClipboardList,
  Columns3,
  ContactRound,
  FileText,
  Headphones,
  LayoutDashboard,
  PackageSearch,
  PlugZap,
  ReceiptText,
  Settings,
  ShoppingCart,
  UserRound,
  type LucideIcon,
} from 'lucide-react'

export type LoaderKind =
  | 'dashboard'
  | 'enquiries'
  | 'walkins'
  | 'sessions'
  | 'products'
  | 'contacts'
  | 'recordings'
  | 'quotes'
  | 'sales-orders'
  | 'purchase-orders'
  | 'invoices'
  | 'pipelines'
  | 'analytics'
  | 'connections'
  | 'settings'

const icons: Record<LoaderKind, LucideIcon> = {
  dashboard: LayoutDashboard,
  enquiries: ClipboardList,
  walkins: UserRound,
  sessions: Headphones,
  products: PackageSearch,
  contacts: ContactRound,
  recordings: AudioLines,
  quotes: FileText,
  'sales-orders': ShoppingCart,
  'purchase-orders': ClipboardCheck,
  invoices: ReceiptText,
  pipelines: Columns3,
  analytics: BarChart3,
  connections: PlugZap,
  settings: Settings,
}

type PageLoaderProps = {
  kind: LoaderKind
  label: string
  detail?: string
  compact?: boolean
}

export default function PageLoader({ kind, label, detail, compact = false }: PageLoaderProps) {
  const Icon = icons[kind] ?? FileText
  return <div className={`page-loader page-loader-${kind}${compact ? ' page-loader-compact' : ''}`} role="status" aria-live="polite">
    <div className="page-loader-visual" aria-hidden="true">
      <span className="page-loader-orbit" />
      <Icon className="page-loader-icon" size={32} strokeWidth={1.8} />
      <span className="page-loader-spark page-loader-spark-one" />
      <span className="page-loader-spark page-loader-spark-two" />
    </div>
    <div className="page-loader-copy"><strong>{label}</strong>{detail && <small>{detail}</small>}</div>
  </div>
}
