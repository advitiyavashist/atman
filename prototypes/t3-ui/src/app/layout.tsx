import "./globals.css";
import type { ReactNode } from "react";
import { Shell } from "@/components/Shell";

export const metadata = {
  title: "Atman T3 prototype (T-1019 throwaway)",
  description: "Not the launch UI. Reads the same board files as tickets ui.",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body>
        <Shell>{children}</Shell>
      </body>
    </html>
  );
}
