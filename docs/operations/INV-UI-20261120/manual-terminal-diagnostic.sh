bash <<'MEZAN_CHECK' 2>&1 | tee /tmp/mezan-inventory-check.log || true
set +e
if ! cd /app; then
  printf '%s\n' 'Cannot open /app'
  exit 0
fi
printf '\n--- Release Guard ---\n'
python scripts/production_release_guard.py status
printf 'GUARD_EXIT=%s\n' "$?"
printf '\n--- Tracked changes ---\n'
git status --short --untracked-files=no
printf '\n--- Branch and commit ---\n'
git branch --show-current
git rev-parse HEAD
printf '\n--- Required commands ---\n'
command -v python python3 git yarn
printf '\n--- End: diagnosis only, no build or publish ---\n'
exit 0
MEZAN_CHECK
