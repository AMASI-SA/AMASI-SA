# TikTok native V2 security guarantees

- OAuth can be started and tested only by the Mezan store owner.
- State is signed, short-lived, persisted, and consumed once.
- Callback completion requires an HttpOnly browser-binding cookie.
- Access tokens are encrypted with a TikTok-specific Fernet key.
- Tokens and app secrets never enter V2 public projections or responses.
- Advertiser discovery is read-only.
- Make data and legacy TikTok collections are forbidden dependencies.
- Campaign, ad, creative, audience, and budget mutations remain blocked by policy.
