import { useEffect, useRef } from 'react'
import {
  FiChevronLeft,
  FiChevronRight,
  FiChevronsLeft,
  FiChevronsRight,
  FiPause,
  FiPlay,
} from 'react-icons/fi'

type Props = {
  value: number
  total: number
  playing: boolean
  onChange: (v: number) => void
  onTogglePlay: () => void
  intervalMs?: number
}

// Wheel delta to accumulate before stepping one frame (smooths trackpad scrolling).
const WHEEL_STEP = 24

export default function Scrubber({
  value,
  total,
  playing,
  onChange,
  onTogglePlay,
  intervalMs = 500,
}: Props) {
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const rootRef = useRef<HTMLDivElement | null>(null)
  const accumRef = useRef(0)

  // Keep latest value/total/onChange in refs so the native wheel listener (attached
  // once) always sees fresh state without re-binding.
  const stateRef = useRef({ value, total, onChange })
  stateRef.current = { value, total, onChange }

  useEffect(() => {
    if (!playing) return
    timerRef.current = setInterval(() => {
      onChange(value + 1 >= total ? 0 : value + 1)
    }, intervalMs)
    return () => {
      if (timerRef.current) clearInterval(timerRef.current)
    }
  }, [playing, value, total, intervalMs, onChange])

  // Scroll over the bar to scrub frames. Native non-passive listener so we can
  // preventDefault and stop the page from scrolling underneath.
  useEffect(() => {
    const el = rootRef.current
    if (!el) return
    const onWheel = (e: WheelEvent) => {
      const { value: v, total: t, onChange: cb } = stateRef.current
      if (t === 0) return
      e.preventDefault()
      accumRef.current += e.deltaY
      let next = v
      while (accumRef.current >= WHEEL_STEP) {
        accumRef.current -= WHEEL_STEP
        next += 1
      }
      while (accumRef.current <= -WHEEL_STEP) {
        accumRef.current += WHEEL_STEP
        next -= 1
      }
      next = Math.max(0, Math.min(t - 1, next))
      if (next !== v) cb(next)
    }
    el.addEventListener('wheel', onWheel, { passive: false })
    return () => el.removeEventListener('wheel', onWheel)
  }, [])

  const last = Math.max(0, total - 1)
  return (
    <div className="scrubber" ref={rootRef} title="Scroll over this bar to scrub frames">
      <button
        onClick={() => onChange(0)}
        disabled={total === 0}
        title="First frame"
        aria-label="First frame"
      >
        <FiChevronsLeft size={16} />
      </button>
      <button
        onClick={() => onChange(Math.max(0, value - 1))}
        disabled={value <= 0}
        title="Previous frame"
        aria-label="Previous frame"
      >
        <FiChevronLeft size={16} />
      </button>
      <button
        onClick={onTogglePlay}
        disabled={total === 0}
        title={playing ? 'Pause' : 'Play'}
        aria-label={playing ? 'Pause' : 'Play'}
      >
        {playing ? <FiPause size={16} /> : <FiPlay size={16} />}
      </button>
      <button
        onClick={() => onChange(Math.min(last, value + 1))}
        disabled={value >= last}
        title="Next frame"
        aria-label="Next frame"
      >
        <FiChevronRight size={16} />
      </button>
      <button
        onClick={() => onChange(last)}
        disabled={total === 0}
        title="Last frame"
        aria-label="Last frame"
      >
        <FiChevronsRight size={16} />
      </button>
      <input
        type="range"
        min={0}
        max={last}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
      />
      <div className="counter">
        {total === 0 ? '0 / 0' : `${value + 1} / ${total}`}
      </div>
    </div>
  )
}
