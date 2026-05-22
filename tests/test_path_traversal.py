import os
import sys
sys.path.insert(0, os.getcwd())
from api_server import app

def test_upload_path_traversal_rejected():
    with app.test_client() as client:
        # Try to upload a file with path traversal in the name
        data = {'file': (b'../../../etc/evil.txt', b'malicious')}
        resp = client.post('/api/file/upload', data=data, content_type='multipart/form-data')
        assert resp.status_code == 400, f"Expected 400, got {resp.status_code}: {resp.data}"
        json_resp = resp.get_json()
        assert not json_resp['success'], "Path traversal should be rejected"

def test_image_upload_path_traversal_rejected():
    with app.test_client() as client:
        data = {'image': (b'../../secret.png', b'fake_png_data')}
        resp = client.post('/api/image/upload', data=data, content_type='multipart/form-data')
        assert resp.status_code == 400, f"Expected 400, got {resp.status_code}: {resp.data}"
        json_resp = resp.get_json()
        assert not json_resp['success'], "Path traversal should be rejected"