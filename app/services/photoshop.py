# photoshop domain — extracted from app/services.py (W1d)
from ._base import *  # noqa: F401,F403
import app.services as _svc  # noqa: E402
from app.core import _photoshop_protocol_error, _photoshop_public_request  # noqa: E402,F401

def _photoshop_request_snapshot(request_id: str) -> Optional[Dict[str, Any]]:
    with PHOTOSHOP_ROUND_TRIP_LOCK:
        _svc._main('_photoshop_prune_requests_locked')()
        record = PHOTOSHOP_ROUND_TRIP_REQUESTS.get(str(request_id or ''))
        if not record:
            return None
        result = copy.deepcopy(record)
        if result.get('state') == 'pending' and _svc._main('now_ms')() >= int(result.get('expires_at_ms') or 0):
            record['state'] = 'expired'
            result['state'] = 'expired'
        if result.get('state') == 'processing':
            result['state'] = 'pending'
        result.pop('expires_at_ms', None)
        return result

def _photoshop_mark_returned(request_id: str, committed: Dict[str, Any]) -> Dict[str, Any]:
    """Publish the one-time result after durable project commit."""
    with PHOTOSHOP_ROUND_TRIP_LOCK:
        current = PHOTOSHOP_ROUND_TRIP_REQUESTS.get(str(request_id or ''))
        if not current:
            raise _photoshop_protocol_error(404, 'REQUEST_NOT_FOUND', 'Photoshop 往返请求不存在')
        current.update({'state': 'returned', 'returned_at': _svc._main('now_ms')() // 1000, 'result_node_id': committed['result_node']['id'], 'result_media_id': committed['media']['asset_id']})
        return _photoshop_public_request(current)

def photoshop_connection_status():
    connected_clients = [client_id for client_id in _svc._main('manager').connection_clients.values() if str(client_id or '').lower().startswith('ps_')]
    connected = bool(connected_clients)
    return {'connected': connected, 'connection_count': len(connected_clients), 'message': 'Photoshop 连接器已连接' if connected else 'Photoshop 连接器未连接'}
