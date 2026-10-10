import "@fontsource/noto-sans-kr/400.css";
import "@fontsource/noto-sans-kr/600.css";
import "./globals.css";
import "./workspace.css";

export const metadata = {
  title: "UV Tape Residue EDS Lab",
  description: "SEM / EDS residue analysis platform",
};

export default function RootLayout({ children }) {
  return <html lang="ko"><body>{children}</body></html>;
}
