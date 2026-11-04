"""Check role ownership in the disposable loopback-only test namespace."""
import sys
from uuid import UUID
sys.path[:0] = ['/opt/acceptance', '/opt/mezan/backend']
from acceptance import boundary, db

if __name__ == '__main__':
    boundary()
    role = sys.argv[1]
    assert role in {'worker-sync', 'worker-accounting'}
    row = db().backend_startup_leases_v1.find_one({
        '_id': 'backend-heavy-initialization:independent-worker:' + role,
        'status': 'running'})
    assert row and row.get('owner_id') and row.get('fence'), 'role claim missing'
    print(str(UUID(row['fence'])))
