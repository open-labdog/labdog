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
// IBM Plex is the typeface of the rail-and-pane redesign: Sans for UI
// text, Mono for every value an operator might copy (hostnames, ports,
// CIDRs, cron lines) and for the uppercase section labels. Plex Sans is
// a variable build so one file covers 400–700; Plex Mono is not, so it
// is the three static weights the UI uses. See app/fonts/README.md for
// licensing and how to update them.
const plexSans = localFont({
  src: './fonts/ibm-plex-sans.woff2',
  variable: '--font-sans',
  weight: '400 700',
  display: 'swap',
})

const plexMono = localFont({
  src: [
    { path: './fonts/ibm-plex-mono-400.woff2', weight: '400', style: 'normal' },
    { path: './fonts/ibm-plex-mono-500.woff2', weight: '500', style: 'normal' },
    { path: './fonts/ibm-plex-mono-600.woff2', weight: '600', style: 'normal' },
  ],
  variable: '--font-mono',
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
        className={`${plexSans.variable} ${plexMono.variable} font-sans antialiased bg-bg text-text`}
      >
        <Providers>
          <AppShell>{children}</AppShell>
        </Providers>
      </body>
    </html>
  )
}
