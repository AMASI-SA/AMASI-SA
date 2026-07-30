/**
 * Compatibility entrypoint for extensionless imports.
 *
 * React Scripts and Jest resolve .js before .jsx, so existing imports keep
 * their stable path while the protected legacy file remains untouched. The
 * focused V2 implementation can be validated independently and the shim can
 * be removed after the historical Ads Manager workflow guard is repaired.
 */
export { default } from "./IntegrationCardV2";
