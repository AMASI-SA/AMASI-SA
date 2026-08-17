// Compatibility source contract for the legal-page workflow during the Vite
// entrypoint migration. Runtime boot remains in index.js; keep the crawler
// directive call visible here until all repository workflows use index.js.
import { applyPublicLegalNoIndex } from "@/publicLegalNoIndex";

applyPublicLegalNoIndex(window.location.pathname);
