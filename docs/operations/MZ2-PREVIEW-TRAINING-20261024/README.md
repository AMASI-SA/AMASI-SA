# Isolated operational Preview training

The September Preview source was replaced with the pinned October source and a Preview-only frontend built in GitHub Actions. No build or dependency installation ran in /app. The shared production checkout was not modified.

Activation occurred only after Release Guard reported inactive. The old Supervisor configuration is retained at /opt/mezan-training-20261024/supervisor.before; the new configuration is supervisor.after. Unrelated service sections were preserved. Runtime files are outside /app and the previous runtime remains intact.

The pre-existing private Preview authentication adapter was updated for the new requires_email_otp client_type keyword and reviewed source hashes. Existing independent signing key, loopback Mongo, exact Preview hostname/origins, blocked external network, disabled startup workers, password checks and role/session checks remain. Legacy signed sessions retain native access; Review V2 authority is not fabricated. Startup succeeded, health returned 200, Production Host returned 403, and the existing browser session opened opening balances and opening stock.

The frontend and backend both identify source fda6ab558023d2c29bb200951ec47fb7a87f1c90. This is not a Production release: no release identity was fabricated. The browser shows zero opening balances. Banks, catalog and storage locations are empty in this training database. Training fixtures and guided unsaved entries remain; no business amount or stock quantity was saved, no setup approval was clicked, no writer enabled.

The candidate on 127.0.0.1:18001 was stopped after verification. Supervisor runs the new frontend and backend on the standard Preview ports. Runtime readiness is not evidence of financial posting correctness or end-to-end stock approval.
