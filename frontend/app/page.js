import dynamic from "next/dynamic";

const App = dynamic(() => import("./AppClient"), { ssr: false });

export default function Page() {
  return <App />;
}
