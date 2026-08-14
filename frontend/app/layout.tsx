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
// Both files are the *variable* latin-subset builds, so one file covers
// the whole weight range instead of one per weight — 68 kB for both.
// See app/fonts/README.md for licensing and how to update them.
const dmSans = localFont({
  src: './fonts/dm-sans.woff2',
  variable: '--font-sans',
  weight: '400 700',
  display: 'swap',
})

const jetbrainsMono = localFont({
  src: './fonts/jetbrains-mono.woff2',
  variable: '--font-mono',
  weight: '400 500',
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
  return (
    <html lang="en" className="dark" suppressHydrationWarning>
      <body
        className={`${dmSans.variable} ${jetbrainsMono.variable} font-sans antialiased bg-slate-950 text-slate-50`}
      >
        <Providers>
          <AppShell>{children}</AppShell>
        </Providers>
      </body>
    </html>
  )
}
