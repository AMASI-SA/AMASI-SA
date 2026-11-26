"""DRAFT ONLY: root must freeze/review every marked value before host use.

No host operations occur on import. DRAFT=True makes every wrapper refuse to
run. Never replace the preceding receipt, continuation or official guard proof.
"""
from pathlib import Path

DRAFT = True
REPO = Path("/app")
BRANCH = "hotfix/prod-snap-meta-final"
BASE = "317dc183485566ba9e05c5afca5d4ea1b84e291e"

# Fill only after exact A/B/D review and the new governed build are complete.
SOURCE = "b7c7d13f9685fdbc5d7c93b8f8278638bcb2aa6d"
INTENT = "a59e6ee18f41d13baaa0e863de2ed5810dd43319"
DEPLOYMENT = "SET_EXACT_MERGED_DEPLOYMENT_D"
TREE = "57601ee93838dde732e122354f0766c7e7414301"
RELEASE_ID = "rg5-3786c214f5c7213c2b3c118ff79d895b716268f479c94d8c5fba2f7a0de66b86"
STAGE_REL = ".git/operational-refresh-completion-b7c7d13f"
ACTOR = "operational-refresh-completion-20261126"

# Pinned previous release. Its original failed-preservation receipt stays intact.
PREVIOUS_REL = ".git/operational-refresh-2133-50cbe07d"
OLD_SOURCE = "50cbe07da4e29fa41c7a184addffdeb8d2169f1a"
OLD_TREE = "a8aedd1be45cf7dcb26b31078a6c5e580709e30f"
OLD_RELEASE_ID = "rg5-5064eab0115d74d39ba29eeed04f13511f5a27156260c0cc5a3a4bc2045e1fb5"
OLD_PUBLICATION_VERSION = "bad38ff"
PREVIOUS_RECEIPT_SHA256 = "0ef141cb905ebd2ca3593cd3bc42d0d78578652e9712637cd6e090816d343d53"
PREVIOUS_PROOF_SHA256 = "108220774d22e0074127aa10d63b1dbd5e35afbaaf87998ac14c6bed37da6c4b"
PREVIOUS_CONTINUATION_SHA256 = "143827cc19bccacb84fbfea33cf31e8e08f52765275b653460d85f4e3d20f959"
PREVIOUS_RESTORED_AT = "2026-11-26T02:41:22.858577+00:00"
OLD_DEPENDENCIES_REL = ".git/payment-release-2130-93cbedb6/generated-before/frontend/node_modules"

# Preserve all old backups. Verify/reuse only the complete dependency subtree
# of this pinned old archive; archive the three current small outputs afresh.
DEPENDENCY_ROOT = "frontend/node_modules"
DEPENDENCY_ARCHIVE_REL = PREVIOUS_REL + "/generated-before.tar.gz"
FRESH_OUTPUTS = ("frontend/build", "frontend/.release", "backend/release_identity.json")
GENERATED = (DEPENDENCY_ROOT, *FRESH_OUTPUTS)
RESERVE = 128 * 1024 * 1024

# These are the only post-publication variance candidates. Nothing is waived
# automatically: exact observations need a separate root-reviewed approval.
CREDENTIAL = "backend/.env"
BOOKKEEPING = ".emergent/stripped_env_names"
MAX_BOOKKEEPING_APPEND_BYTES = 4096
PRESERVATION_OBSERVATION = "postpublication-preservation.json"
PRESERVATION_APPROVAL = "reviewed-preservation-approval.json"
