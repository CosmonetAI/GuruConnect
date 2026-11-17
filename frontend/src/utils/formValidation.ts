export const MAX_PHONE_DIGITS = 10
export const MAX_COUNTRY_CODE_DIGITS = 3
export const MAX_TEXT_LENGTH = 255
export const MAX_NOTE_LENGTH = 2_000

export function sanitizeTextInput(value: string, maxLength = MAX_TEXT_LENGTH): string {
  return value.slice(0, maxLength)
}

export function sanitizePhoneNumber(value: string, maxDigits = MAX_PHONE_DIGITS): string {
  const hasPlus = value.trimStart().startsWith('+')
  const digits = value.replace(/\D/g, '').slice(0, maxDigits)
  return hasPlus ? `+${digits}` : digits
}

export function validatePhoneNumber(value: string, requireCountryCode = false): string | undefined {
  const trimmed = value.trim()
  if (!trimmed) return 'Phone number is required.'
  const digits = trimmed.replace(/\D/g, '')
  const compact = trimmed.replace(/[\s().-]/g, '')
  if ((requireCountryCode || trimmed.startsWith('+')) && !/^\+\d{1,3}\d{10}$/.test(compact)) {
    return 'Enter a country code followed by 10 digits.'
  }
  if (digits.length !== MAX_PHONE_DIGITS && !trimmed.startsWith('+')) {
    return 'Enter exactly 10 digits.'
  }
  if (!/^\+?[0-9\s().-]+$/.test(trimmed)) return 'Use numbers only, with an optional country code.'
  return undefined
}

export function validateEmail(value: string, required = false): string | undefined {
  const trimmed = value.trim()
  if (!trimmed) return required ? 'Email address is required.' : undefined
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(trimmed) ? undefined : 'Enter a valid email address.'
}

export function validateText(value: string, label: string, options: { required?: boolean; maxLength?: number; minLength?: number } = {}): string | undefined {
  const trimmed = value.trim()
  if (!trimmed && options.required) return `${label} is required.`
  if (options.minLength !== undefined && trimmed.length > 0 && trimmed.length < options.minLength) return `${label} must be at least ${options.minLength} characters.`
  if (options.maxLength !== undefined && value.length > options.maxLength) return `${label} must be ${options.maxLength} characters or fewer.`
  return undefined
}