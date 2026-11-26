/** Display customer names consistently without changing the stored value. */
export function formatCustomerName(value: string | null | undefined): string {
  return String(value || '').trim().split(/\s+/).filter(Boolean).map(formatNamePart).join(' ')
}

function formatNamePart(value: string): string {
  return value.split(/([-'’])/).map(part => {
    if (/^[-'’]$/.test(part)) return part
    return part ? part.charAt(0).toUpperCase() + part.slice(1).toLowerCase() : part
  }).join('')
}
