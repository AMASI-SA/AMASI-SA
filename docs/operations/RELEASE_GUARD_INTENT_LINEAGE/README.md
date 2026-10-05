# Release Guard Intent lineage repair (Draft)

Independent Release/CI-only task. Preserve byte identity, file type/mode and all existing Source/Release identity checks. Replace blanket full-history rejection with a shared fail-closed provenance proof; only approved Production checkpoint imports may change the inherited Intent during a two-parent merge. Real temporary Git repository tests must cover edits/reverts, side branches, deletion, modes, incomplete history and untrusted checkpoints.

Base: a7977f4cfc1ef0a721fc25d783661332f32fe3b6. No operational code, PR1263/1264, Production branch, current Intent, release ID, artifact or lease changes. No Prepare/Prepublish/Deploy. Production writes=0.

Status: documentation bootstrap only; create Draft PR before implementation. Trust provenance must not be inferred from branch names, PR numbers, commit messages or final diff alone.
