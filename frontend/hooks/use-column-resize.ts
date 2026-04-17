"use client"

export function useColumnResize(onChange: (key: string, width: number) => void) {
  function startResize(key: string, startWidth: number) {
    return (e: React.MouseEvent) => {
      e.preventDefault()
      e.stopPropagation()
      const startX = e.clientX
      const body = document.body
      body.style.cursor = "col-resize"
      body.style.userSelect = "none"

      const move = (me: MouseEvent) => {
        const delta = me.clientX - startX
        onChange(key, Math.max(60, startWidth + delta))
      }
      const up = () => {
        body.style.cursor = ""
        body.style.userSelect = ""
        document.removeEventListener("mousemove", move)
        document.removeEventListener("mouseup", up)
      }
      document.addEventListener("mousemove", move)
      document.addEventListener("mouseup", up)
    }
  }

  return { startResize }
}
