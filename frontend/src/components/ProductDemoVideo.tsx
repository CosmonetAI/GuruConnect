import { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { Play, X } from 'lucide-react'
import '../styles/product-demo-video.css'

export default function ProductDemoVideo() {
  const [open, setOpen] = useState(false)
  const dialogRef = useRef<HTMLDialogElement>(null)

  useEffect(() => {
    if (!open) return
    const dialog = dialogRef.current
    const previousOverflow = document.body.style.overflow
    dialog?.showModal()
    document.body.style.overflow = 'hidden'
    return () => {
      dialog?.close()
      document.body.style.overflow = previousOverflow
    }
  }, [open])

  return <>
    <button type="button" className="product-demo-trigger" aria-haspopup="dialog" onClick={() => setOpen(true)}>
      <span className="product-demo-play"><Play size={19} fill="currentColor" aria-hidden="true" /></span>
      <span><strong>Watch product demo</strong><small>See the complete workflow · 8 min 40 sec</small></span>
    </button>
    {open && createPortal(<dialog ref={dialogRef} className="product-demo-dialog" aria-labelledby="product-demo-title"
      onClose={() => setOpen(false)}
      onClick={event => { if (event.target === event.currentTarget) dialogRef.current?.close() }}>
      <div className="product-demo-content">
        <header className="product-demo-header">
          <div><span>CONNECTCOSMOS</span><h2 id="product-demo-title">From enquiry to invoice</h2></div>
          <button type="button" className="product-demo-close" aria-label="Close product demo" autoFocus onClick={() => dialogRef.current?.close()}><X size={22} aria-hidden="true" /></button>
        </header>
        <div className="product-demo-player">
          <iframe src="https://www.youtube-nocookie.com/embed/Q2BxBEzkkRk?autoplay=1&rel=0&playsinline=1"
            title="ConnectCosmos product demo"
            allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share"
            referrerPolicy="strict-origin-when-cross-origin" allowFullScreen />
        </div>
      </div>
    </dialog>, document.body)}
  </>
}
