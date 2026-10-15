import { useEffect } from 'react'

// Some imported GG strings were saved after a UTF-8/Windows-1252 decoding
// mismatch. Repair only the rendered Zoho surface, including late React
// updates, while keeping real credentials out of the UI.
const replacements: Array<[string, string]> = [
  // Common UTF-8 bytes decoded as Windows-1252. Keep these before the
  // legacy mappings below so the live wizard never exposes mojibake.
  ['\u00e2\u20ac\u201d', '—'],
  ['\u00e2\u20ac\u201c', '–'],
  ['\u00e2\u20ac\u00a2', '•'],
  ['\u00e2\u20ac\u2122', '’'],
  ['\u00c3\u00a2\u00e2\u201a\u00ac\u00c2\u00a2', '********'],
  ['\u00e2\u20ac\u00a2', '*'],
  ['\u00c3\u00a2\u00e2\u201a\u00ac\u00c2\u00a6', '...'],
  ['\u00e2\u20ac\u00a6', '...'],
  ['\u00c3\u00a2\u00e2\u201a\u00ac\u00e2\u20ac\u009d', '-'],
  ['\u00e2\u20ac\u2014', '-'],
  ['\u00e2\u20ac\u2122', "'"],
  ['\u00e2\u0153\u201c', 'OK'],
  ['\u00e2\u0153\u00a6', '*'],
  ['\u00e2\u2020\u2014', '->'],
  ['\u00e2\u0152\u201e', 'x'],
  ['\u00e2\u2013\u00bd', '▼'],
  ['\u00e2\u2013\u00a1', '◆'],
  ['\u00e2\u2013\u00a3', '■'],
  ['\u00e2\u2013\u00a4', '◆'],
  ['\u00e2\u2013\u00a5', '◆'],
  ['\u00e2\u2013\u00b1', '◆'],
  ['\u00e2\u2014\u008f', '●'],
  ['\u00e2\u2014\u2030', '◉'],
  ['\u00e2\u2014\u2039', '○'],
  ['\u00e2\u2014\u02c6', '◆'],
  ['\u00e2\u2122\u2122', '♣'],
  ['\u00c3\u2014', 'x'],
  ['\u00c2\u00b7', '·'],
  ['\u00e2\u201a\u00b9', 'INR '],
]

function repair(value: string) {
  return replacements.reduce((result, [bad, good]) => result.split(bad).join(good), value)
}

function repairNode(root: Node) {
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT)
  const textNodes: Text[] = []
  let node: Node | null
  while ((node = walker.nextNode())) textNodes.push(node as Text)
  textNodes.forEach(text => { text.nodeValue = repair(text.nodeValue || '') })
  if (root instanceof Element) {
    root.querySelectorAll<HTMLElement>('*').forEach(element => {
      for (const attribute of ['placeholder', 'title', 'aria-label']) {
        const value = element.getAttribute(attribute)
        if (value) element.setAttribute(attribute, repair(value))
      }
    })
  }
}

export default function EncodingRepair() {
  useEffect(() => {
    const root = document.querySelector('.setup-page')?.parentElement || document.body
    repairNode(root)
    const observer = new MutationObserver(() => { observer.disconnect(); repairNode(root); observer.observe(root, { childList: true, subtree: true }) })
    observer.observe(root, { childList: true, subtree: true })
    return () => observer.disconnect()
  }, [])
  return null
}
