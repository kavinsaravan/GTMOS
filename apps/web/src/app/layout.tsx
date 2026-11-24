import type { Metadata } from "next";
import Link from "next/link";
import { Geist, Geist_Mono, Newsreader } from "next/font/google";
import "./globals.css";

const geistSans = Geist({ variable: "--font-geist-sans", subsets: ["latin"] });
const geistMono = Geist_Mono({ variable: "--font-geist-mono", subsets: ["latin"] });
const serif = Newsreader({ variable: "--font-newsreader", subsets: ["latin"] });

export const metadata: Metadata = {
  title: "GTMOS — Find your next customers",
  description: "Evidence-backed lead discovery and human-approved outreach.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className={`${geistSans.variable} ${geistMono.variable} ${serif.variable} h-full antialiased`}>
      <body className="flex min-h-full flex-col">
        <header className="border-b border-stone-200 bg-white">
          <div className="mx-auto flex max-w-7xl items-center justify-between px-6 py-3">
            <Link href="/" className="font-serif text-xl font-semibold tracking-tight text-stone-900">
              GTMOS
            </Link>
            <nav className="flex items-center gap-4 text-sm text-stone-600">
              <Link href="/campaigns" className="hover:text-stone-900">
                Campaigns
              </Link>
              <Link href="/" className="rounded-lg bg-stone-900 px-3 py-1.5 font-medium text-white hover:bg-stone-700">
                New campaign
              </Link>
            </nav>
          </div>
        </header>
        <main className="mx-auto w-full max-w-7xl flex-1 px-6 py-8">{children}</main>
      </body>
    </html>
  );
}
