# Run only after the new Emergent deployment explicitly reports Deployment Succeeded.
cd /app &&
PYTHONDONTWRITEBYTECODE=1 python -B scripts/production_release_guard.py verify --url https://mezansalla.com &&
PYTHONDONTWRITEBYTECODE=1 python -B scripts/production_release_guard.py status
