import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "CircuitPilot · Digital hardware workspace",
  description: "A persistent engineering workspace for digital circuit design.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}

