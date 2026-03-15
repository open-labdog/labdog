import { NextResponse } from "next/server"
import type { NextRequest } from "next/server"

const PUBLIC_PATHS = ["/login", "/register"]

export function middleware(request: NextRequest) {
  const { pathname } = request.nextUrl
  const isPublic = PUBLIC_PATHS.some(p => pathname.startsWith(p))
  const authCookie = request.cookies.get("barricade_auth")
  if (!isPublic && !authCookie) {
    return NextResponse.redirect(new URL("/login", request.url))
  }
  if (isPublic && authCookie) {
    return NextResponse.redirect(new URL("/dashboard", request.url))
  }
  return NextResponse.next()
}

export const config = {
  matcher: ["/((?!api|_next/static|_next/image|favicon.ico).*)"],
}
