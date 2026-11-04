import { Suspense } from "react";
import PageLoadingScreen from "./PageLoadingScreen";

export default function PageContentBoundary({ children }) {
    return <Suspense fallback={<PageLoadingScreen />}>{children}</Suspense>;
}
