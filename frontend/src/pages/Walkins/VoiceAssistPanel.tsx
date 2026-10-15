import { Mic2, Sparkles } from 'lucide-react'
import ConversationRecorder from '../Enquiries/ConversationRecorder'
import type { VisitorField } from '../../voice/extract'
import '../../styles/recording.css'

export interface VoiceAssistPanelProps {
  customerName: string
  onConversationChange: (id: string | null) => void
  onBusyChange: (busy: boolean) => void
  // Backwards-compatible optional props (ignored by the compact UI)
  values?: Record<VisitorField, string>
  validate?: (field: VisitorField, value: string) => string | null
  onApply?: (field: VisitorField, value: string) => void
  onAiFieldsChange?: (fields: VisitorField[]) => void
}

export default function VoiceAssistPanel({ customerName, onConversationChange, onBusyChange }: VoiceAssistPanelProps) {
  return (
    <section className="walkin-card voice-assist compact" aria-labelledby="voice-assist-title">
      <div className="walkin-section-heading"><span><Sparkles size={16} /></span><div><h2 id="voice-assist-title">Voice assist</h2></div></div>
      <div className="voice-compact" style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
        <Mic2 size={28} aria-hidden />
        <ConversationRecorder customerName={customerName.trim() || 'Walk-in visitor'} onConversationChange={onConversationChange} onBusyChange={onBusyChange} compact />
      </div>
    </section>
  )
}
