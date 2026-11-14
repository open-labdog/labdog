/** The name "unbork" as drawn in the logo: Familjen Grotesk Bold, set
 *  tight, outlined so the app needs no third font file for six letters.
 *  Familjen Grotesk is SIL Open Font License 1.1. The outlines match the
 *  logo files, so regenerate both together if the wordmark changes.
 *
 *  `size` is the font size in px the outlines stand in for; the box is
 *  ascender to descender, so it centres beside the mark the way text of
 *  that size would. It takes the current text colour. */

const VIEW_W = 179.1
const VIEW_H = 40.75
const EM = 60

const PATH =
  "M10.1 40.75Q5.1 40.75 2.55 37.6Q0 34.45 0 29.05V10H8.1V27.7Q8.1 30.75 9.2 32.45Q10.3 34.15 12.75 34.15Q15.7 34.15 17.3 31.85Q18.9 29.55 18.9 26.2V10H27V40H19.7V35.5H18.5Q17.75 37.75 15.75 39.25Q13.75 40.75 10.1 40.75ZM32.6 40V10H40.1V14.5H41.3Q42.2 12.25 44.08 10.75Q45.95 9.25 49.6 9.25Q54.6 9.25 57.1 12.4Q59.6 15.55 59.6 20.95V40H51.5V22.3Q51.5 19.2 50.4 17.52Q49.3 15.85 46.8 15.85Q43.9 15.85 42.3 18.12Q40.7 20.4 40.7 23.8V40ZM81.9 40.6Q78.55 40.6 76.55 39.3Q74.55 38 73.5 36.1H72.3V40H64.8V0H72.9V13.3H74.1Q75.2 11.5 77.1 10.45Q79 9.4 81.9 9.4Q85.9 9.4 88.58 11.35Q91.25 13.3 92.58 16.8Q93.9 20.3 93.9 24.95Q93.9 29.45 92.6 33Q91.3 36.55 88.65 38.58Q86 40.6 81.9 40.6ZM79.6 34.9Q82.35 34.9 83.92 32.42Q85.5 29.95 85.5 25Q85.5 20.05 83.92 17.57Q82.35 15.1 79.6 15.1Q76.55 15.1 74.72 17.75Q72.9 20.4 72.9 25Q72.9 29.55 74.72 32.22Q76.55 34.9 79.6 34.9ZM110.3 40.75Q102.85 40.75 99.18 36.48Q95.5 32.2 95.5 25Q95.5 17.8 99.18 13.52Q102.85 9.25 110.3 9.25Q117.75 9.25 121.43 13.52Q125.1 17.8 125.1 25Q125.1 32.2 121.43 36.48Q117.75 40.75 110.3 40.75ZM110.3 35.35Q113.6 35.35 115.15 32.75Q116.7 30.15 116.7 24.95Q116.7 19.8 115.15 17.22Q113.6 14.65 110.3 14.65Q107 14.65 105.45 17.22Q103.9 19.8 103.9 24.95Q103.9 30.15 105.45 32.75Q107 35.35 110.3 35.35ZM128.6 40V10H136V15.5H137.2Q137.5 14 138.32 12.67Q139.15 11.35 140.85 10.52Q142.55 9.7 145.4 9.7H147V17H144.8Q140.55 17 138.62 19.15Q136.7 21.3 136.7 25.6V40ZM150.4 40V0H158.5V21.5H160.9L169.3 10H177.9V10.3L167.65 24L179.1 39.7V40H169.65L160.9 27.5H158.5V40Z"

export function BrandWordmark({ size = 13, className }: { size?: number; className?: string }) {
  return (
    <svg
      role="img"
      aria-label="unbork"
      viewBox={`0 0 ${VIEW_W} ${VIEW_H}`}
      width={(size * VIEW_W) / EM}
      height={(size * VIEW_H) / EM}
      fill="currentColor"
      className={className}
    >
      <path d={PATH} />
    </svg>
  )
}
