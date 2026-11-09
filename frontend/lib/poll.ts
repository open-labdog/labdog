/** Run `fn` now and every `ms` while the page is visible, for a
 *  `useEffect` (it returns the cleanup). A hidden tab stops the timer and
 *  coming back runs `fn` at once, as TanStack Query's pollers do
 *  (`refetchIntervalInBackground` is false by default). */
export function pollWhileVisible(fn: () => void, ms: number): () => void {
  let timer: ReturnType<typeof setInterval> | undefined
  const stop = () => {
    if (timer !== undefined) clearInterval(timer)
    timer = undefined
  }
  const start = () => {
    stop()
    fn()
    timer = setInterval(fn, ms)
  }
  const onVisibility = () => (document.hidden ? stop() : start())
  if (!document.hidden) start()
  document.addEventListener("visibilitychange", onVisibility)
  return () => {
    stop()
    document.removeEventListener("visibilitychange", onVisibility)
  }
}
