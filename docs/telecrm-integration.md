# TeleCRM integration

## Webhook endpoint

Use this base URL for the integration:

```text
https://gg-backend-0vh9.onrender.com/api/webhooks/telecrm
```

Configure the three webhooks with these event-specific endpoints:

```text
POST https://gg-backend-0vh9.onrender.com/api/webhooks/telecrm/call-recording
POST https://gg-backend-0vh9.onrender.com/api/webhooks/telecrm/whatsapp-incoming
POST https://gg-backend-0vh9.onrender.com/api/webhooks/telecrm/whatsapp-status
```

Headers:

```http
Content-Type: application/json
Authorization: Bearer <TELECRM_KEY>
```

TeleCRM may use the equivalent header instead:

```http
X-TeleCRM-Key: <TELECRM_KEY>
```

`TELECRM_KEY` is configured on the backend deployment. Requests without the
correct key receive `401`. The endpoint stores the original JSON payload and
normalized searchable fields in `public.telecrm_events`.

Retries are safe: the same call or message delivery is stored only once.

## 1. Call recording webhook

Event: `call_recording_available`

Required fields: `recording_url`, `customer_phone`, `call_started_at`,
`call_ended_at`, `duration_seconds`. All other fields are optional.

```json
{
  "event": "call_recording_available",
  "call_id": "{{ACTION_callId}}",
  "recording_url": "{{ACTION_callRecordingUrl}}",
  "agent_id": "{{assignee_id}}",
  "agent_name": "{{assignee_name}}",
  "agent_email": "{{assignee_email}}",
  "call_started_at": "{{ACTION_creationTimestamp}}",
  "call_ended_at": "{{ACTION_endTimestamp}}",
  "direction": "{{ACTION_type}}",
  "duration_seconds": "{{ACTION_duration}}",
  "call_status": "{{ACTION_status}}",
  "customer_phone": "{{Phone}}",
  "customer_name": "{{Name}}",
  "lead_id": "{{lead_id}}",
  "call_summary": "{{ACTION_callSummary}}",
  "call_transcript": "{{ACTION_callTranscript}}",
  "call_outcome": "{{ACTION_callOutcome}}",
  "customer_requirements": "{{ACTION_customerRequirements}}",
  "customer_sentiment": "{{ACTION_customerSentiment}}",
  "next_action": "{{ACTION_nextAction}}",
  "follow_up_required": "{{ACTION_followUpRequired}}"
}
```

Expected response:

```json
{
  "accepted": true,
  "duplicate": false,
  "event_id": "<stored-event-uuid>",
  "event": "call_recording_available"
}
```

## 2. Incoming WhatsApp message webhook

Event: `whatsapp_message_received`

Required fields: `customer_phone`, `message_text`, `message_id`, `received_at`.
All other fields are optional.

```json
{
  "event": "whatsapp_message_received",
  "message_id": "{{MESSAGE_id}}",
  "conversation_id": "{{CONVERSATION_id}}",
  "customer_phone": "{{Phone}}",
  "customer_name": "{{Name}}",
  "message_type": "{{MESSAGE_type}}",
  "message_text": "{{MESSAGE_text}}",
  "media_url": "{{MESSAGE_mediaUrl}}",
  "received_at": "{{MESSAGE_timestamp}}",
  "lead_id": "{{lead_id}}",
  "agent_id": "{{assignee_id}}",
  "agent_email": "{{assignee_email}}"
}
```

Expected response:

```json
{
  "accepted": true,
  "duplicate": false,
  "event_id": "<stored-event-uuid>",
  "event": "whatsapp_message_received"
}
```

## 3. WhatsApp delivery-status webhook

Event: `whatsapp_message_status`

Required fields: `customer_phone`, `message_id`, `status`. Timestamps and
failure details are stored when supplied.

```json
{
  "event": "whatsapp_message_status",
  "message_id": "{{MESSAGE_id}}",
  "customer_phone": "{{Phone}}",
  "status": "{{MESSAGE_status}}",
  "sent_at": "{{MESSAGE_sentAt}}",
  "delivered_at": "{{MESSAGE_deliveredAt}}",
  "read_at": "{{MESSAGE_readAt}}",
  "failed_at": "{{MESSAGE_failedAt}}",
  "failure_reason": "{{MESSAGE_errorMessage}}"
}
```

Expected response:

```json
{
  "accepted": true,
  "duplicate": false,
  "event_id": "<stored-event-uuid>",
  "event": "whatsapp_message_status"
}
```

The base endpoint `POST /api/webhooks/telecrm` is also available when a single
TeleCRM webhook URL is preferred; the payload `event` value selects the type.

## Later TeleCRM API work

`whatsapp_message_sent` is not a webhook in the current integration.
Sending WhatsApp messages will be implemented later as a synchronous API
integration. The send API endpoint and credentials are not enabled yet.

### Get a sent WhatsApp message — planned, not enabled

The future request will use the TeleCRM contract, for example:

```json
{
  "event": "whatsapp_message_sent",
  "message_id": "{{MESSAGE_id}}",
  "conversation_id": "{{CONVERSATION_id}}",
  "customer_phone": "{{Phone}}",
  "customer_name": "{{Name}}",
  "message_type": "{{MESSAGE_type}}",
  "message_text": "{{MESSAGE_text}}",
  "media_url": "{{MESSAGE_mediaUrl}}",
  "sent_at": "{{MESSAGE_timestamp}}",
  "agent_id": "{{assignee_id}}",
  "agent_email": "{{assignee_email}}",
  "lead_id": "{{lead_id}}"
}
```

Expected TeleCRM response:

```json
{
  "success": true,
  "message_id": "MSG003",
  "conversation_id": "CONV123",
  "direction": "outgoing",
  "message_type": "text",
  "message": "Sure, how can I help you?",
  "timestamp": "2026-11-25T12:35:00Z",
  "status": "sent"
}
```

### Get complete conversation history — planned, not enabled

The future TeleCRM history API will use:

```json
{
  "phone": "+919876543210",
  "limit": 50,
  "page": 1
}
```

Expected TeleCRM response:

```json
{
  "success": true,
  "conversation_id": "CONV123",
  "messages": [
    {
      "message_id": "MSG001",
      "direction": "incoming",
      "message_type": "text",
      "message": "I am interested in your product.",
      "timestamp": "2026-11-25T12:30:00Z"
    },
    {
      "message_id": "MSG002",
      "direction": "outgoing",
      "message_type": "text",
      "message": "Sure, how can I help you?",
      "timestamp": "2026-11-25T12:35:00Z",
      "status": "sent"
    }
  ]
}
```
