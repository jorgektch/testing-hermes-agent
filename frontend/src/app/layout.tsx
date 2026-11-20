import type { Metadata } from 'next';
import './globals.css';
export const metadata: Metadata = {
  title: 'Hermes Agent · Real Estate Sales Lab',
  description: 'An interactive, synthetic real-estate sales agent experiment using Hermes and OpenRouter.',
  robots: { index: false, follow: false },
};
export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en"><body>{children}</body></html>;
}
