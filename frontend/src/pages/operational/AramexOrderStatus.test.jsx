import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import AramexOrderStatus from './AramexOrderStatus';
import api from '../../lib/api';

jest.mock('../../lib/api', () => ({ post: jest.fn() }));
global.IS_REACT_ACT_ENVIRONMENT = true;

test('previews distinct orders before confirmation and reuses its reference across a failed response', async () => {
    const node = document.createElement('div');
    document.body.appendChild(node);
    const root = createRoot(node);
    Object.defineProperty(window, 'crypto', { configurable: true, value: { randomUUID: () => 'test-command' } });
    const summary = { count: 2, total: '69.00', existing: 1, skipped: [], preview_hash: 'digest' };
    api.post.mockResolvedValueOnce({ data: summary }).mockRejectedValueOnce(new Error('unknown response'))
        .mockResolvedValueOnce({ data: { ...summary, applied: true } });
    try {
        await act(async () => root.render(<AramexOrderStatus sessionScope="owner-scope" />));
        const input = node.querySelector('textarea');
        await act(async () => {
            Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set.call(input, '10 20 20');
            input.dispatchEvent(new Event('input', { bubbles: true }));
        });
        await act(async () => node.querySelector('button').click());
        expect(api.post.mock.calls[0][1]).toEqual({ expected_session_scope: 'owner-scope', order_numbers: ['10', '20'] });
        expect(node.textContent).toContain('جاهز للخصم');
        const confirm = node.querySelector('[role="status"] button');
        await act(async () => confirm.click());
        await act(async () => confirm.click());
        expect(api.post.mock.calls[2][1]).toEqual({ ...api.post.mock.calls[0][1], preview_hash: 'digest', request_id: 'test-command' });
        expect(api.post.mock.calls[1]).toEqual(api.post.mock.calls[2]);
        expect(node.textContent).toContain('خُصم 2');
        expect(node.querySelector('[role="status"] button')).toBeNull();
    } finally {
        await act(async () => root.unmount());
        node.remove();
    }
});
