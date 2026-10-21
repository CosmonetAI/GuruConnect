import { Mic2, Sparkles } from 'lucide-react'
import ConversationRecorder from '../Enquiries/ConversationRecorder'
import type { VisitorField } from '../../voice/extract'
import './Walkins.css'

export interface VoiceAssistPanelProps {
  customerName: string
  customerPhone?: string
  customerEmail?: string
  onConversationChange: (id: string | null) => void
  onBusyChange: (busy: boolean) => void
  values?: Record<VisitorField, string>
  validate?: (field: VisitorField, value: string) => string | null
  onApply?: (field: VisitorField, value: string) => void
  onAiFieldsChange?: (fields: VisitorField[]) => void
}

export default function VoiceAssistPanel({ customerName, customerPhone, customerEmail, onConversationChange, onBusyChange }: VoiceAssistPanelProps) {
  return (
    <section className="walkin-card voice-assist voice-assist-simple" aria-labelledby="voice-assist-title">
      <div className="walkin-section-heading">
        <span><Sparkles size={16} /></span>
        <div>
          <h2 id="voice-assist-title">Voice assist</h2>
          <p>Use the microphone to record the visitor conversation.</p>
        </div>
      </div>
      <div className="voice-simple-recorder">
        <Mic2 size={30} aria-hidden />
        <ConversationRecorder
          customerName={customerName.trim() || 'Walk-in visitor'} customerPhone={customerPhone} customerEmail={customerEmail}
          title="Walk-in registration"
          onConversationChange={onConversationChange}
          onBusyChange={onBusyChange}
          chunkSeconds={30}
          compact
        />
      </div>
    </section>
  )
}
