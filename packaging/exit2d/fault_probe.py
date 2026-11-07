"""Authenticated employee operations during report failure; isolated fixtures only."""
import sys
sys.path[:0] = ['/opt/acceptance', '/opt/mezan/backend']
from acceptance import boundary, db, employee_session, request

if __name__ == '__main__':
    boundary()
    session = employee_session({'employee': 'exit2c-employee'})
    for path in ('/api/supplier-dispatch-v1/workspace?grain=piece',
                 '/api/preparation-work-v1/assembly/orders?state=in_progress',
                 '/api/fulfillment-v2/ready-to-ship'):
        request(0, 'GET', path, cookie=session)
    if sys.argv[1:] == ['reports-down']:
        response = request(0, 'GET', '/api/dashboard', cookie=session, expected=503)
        assert response.json()['detail']['code'] == 'report_worker_unavailable'
        assert response.headers['retry-after'] == '5'
    print('PASS genuine employee operational routes survive independent service outage')
