import pytest
from flask import Flask
import os
import json
from api_server import app, UPLOAD_DIR, BASE_DIR

@pytest.fixture
def client():
    app.config['TESTING'] = True
    with app.test_client() as client:
        yield client

def test_path_traversal_read(client):
    """
    Forsøger at læse en fil uden for UPLOAD_DIR ved hjælp af path traversal.
    Vi forsøger at læse api_server.py selv (som ligger i BASE_DIR).
    """
    # Vi konstruerer en sti der går op fra uploads/ til base dir
    traversal_path = os.path.join("..", "api_server.py")
    
    response = client.post("/api/file/read", json={
        "filepath": traversal_path
    })
    
    # Hvis sårbarheden findes, vil den returnere 200 OK og indholdet af filen.
    # Hvis den er fikset, bør den returnere 403 eller 404 (afhængig af implementering).
    assert response.status_code != 200, f"Sårbarhed fundet! Kunne læse {traversal_path} via /api/file/read"

def test_path_traversal_list(client):
    """
    Forsøger at liste indholdet af en mappe uden for projektets BASE_DIR.
    """
    # Path traversal uden for BASE_DIR bør afvises (C:\Dev eller tilsvarende)
    parent = os.path.dirname(BASE_DIR)
    if parent and parent != BASE_DIR:
        traversal_path = parent  # går én mappe op (uden for BASE_DIR)
        response = client.post("/api/folder/list", json={
            "path": traversal_path
        })
        assert response.status_code == 403, f"Sårbarhed fundet! Kunne liste {traversal_path} via /api/folder/list"

def test_list_base_dir_allowed(client):
    """
    Det er IKKE en sårbarhed at liste BASE_DIR's eget indhold.
    """
    response = client.post("/api/folder/list", json={
        "path": BASE_DIR
    })
    assert response.status_code == 200, "BASE_DIR skal kunne listes"
