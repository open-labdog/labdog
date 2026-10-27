import type { Metadata } from 'next'
import localFont from 'next/font/local'
import './globals.css'
import { Providers } from './providers'
import { AppShell } from '@/components/app-shell'

// Served from the repo rather than fetched from Google at build time.
// `next/font/google` downloads the font while compiling, which made every
// production build depend on reaching fonts.gstatic.com — and when it
// could not, Turbopack failed with a dozen `Can't resolve
// '@vercel/turbopack-next/internal/font/google/font'` errors that name
// neither the network nor the font. CI hit it, and a build that fails for
// reasons unrelated to the diff teaches you to re-run without reading.
//
// Atkinson Hyperlegible is the typeface: Next for UI text and the
// uppercase section labels, Mono for every value an operator might copy
// (hostnames, ports, CIDRs, cron lines). Both are drawn so that easily
// confused shapes — l/I/1, O/0, rn/m — stay distinct, which is most of
// what reading a hostname or an address needs. Each is one variable file
// covering 200–800. See app/fonts/README.md for licensing and how to
// update them.
const atkinsonSans = localFont({
  src: './fonts/atkinson-hyperlegible-next.woff2',
  variable: '--font-sans',
  weight: '200 800',
  display: 'swap',
})

const atkinsonMono = localFont({
  src: './fonts/atkinson-hyperlegible-mono.woff2',
  variable: '--font-mono',
  weight: '200 800',
  display: 'swap',
})

export const metadata: Metadata = {
  title: 'LabDog',
  description: 'Self-hosted Linux configuration management',
}

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode
}>) {
  // No hardcoded theme class here: next-themes (see providers.tsx) writes
  // both `class` and `data-theme` on <html> before hydration, from the
  // `labdog:theme` key in localStorage. Dark is the default; light is a
  // real second theme, not an inversion — see globals.css.
  return (
    <html lang="en" suppressHydrationWarning>
      <body
        className={`${atkinsonSans.variable} ${atkinsonMono.variable} font-sans antialiased bg-bg text-text`}
      >
        <Providers>
          <AppShell>{children}</AppShell>
        </Providers>
      </body>
    </html>
  )
}
