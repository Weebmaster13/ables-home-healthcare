"""Access-control regression tests: roles, ownership, CSRF, traversal."""
import io
import uuid

from werkzeug.datastructures import FileStorage

from app import app
from extensions import db
from models import ClientDocument, User
from routes import allowed_document, save_private_upload
from test_core_features import create_user, login
from werkzeug.security import generate_password_hash


def _login_as(role):
    with app.app_context():
        user, password = create_user(role)
        uid = user.id
    return uid, password


def _cleanup(*ids):
    with app.app_context():
        User.query.filter(User.id.in_(ids)).delete()
        db.session.commit()


def test_client_cannot_open_admin_users():
    app.config["WTF_CSRF_ENABLED"] = False
    uid, password = _login_as("client")
    with app.app_context():
        email = db.session.get(User, uid).email
    try:
        with app.test_client() as client:
            assert login(client, _ns(email, uid), password).status_code == 302
            assert client.get("/admin/users").status_code == 403
    finally:
        _cleanup(uid)


class _ns:
    def __init__(self, email, uid):
        self.email, self.id = email, uid


def test_cross_user_document_download_forbidden(tmp_path, monkeypatch):
    app.config["WTF_CSRF_ENABLED"] = False
    monkeypatch.setitem(app.config, "UPLOAD_FOLDER", str(tmp_path))
    (tmp_path / "documents").mkdir()
    (tmp_path / "documents" / "stored.pdf").write_bytes(b"%PDF-fake")
    with app.app_context():
        owner, owner_pw = create_user("client")
        other, other_pw = create_user("client")
        admin, admin_pw = create_user("admin")
        doc = ClientDocument(owner_id=owner.id, uploaded_by_id=owner.id,
                             title="t", filename="stored.pdf",
                             original_filename="orig.pdf")
        db.session.add(doc)
        db.session.commit()
        did = doc.id
        addrs = {owner.id: (owner.email, owner_pw),
                 other.id: (other.email, other_pw),
                 admin.id: (admin.email, admin_pw)}
    try:
        with app.test_client() as client:
            # stranger -> 403
            email, pw = addrs[other.id]
            login(client, _ns(email, other.id), pw)
            assert client.get(f"/documents/{did}/download").status_code == 403
            client.get("/auth/logout")
            # owner -> 200
            email, pw = addrs[owner.id]
            login(client, _ns(email, owner.id), pw)
            assert client.get(f"/documents/{did}/download").status_code == 200
            client.get("/auth/logout")
            # admin -> 200
            email, pw = addrs[admin.id]
            login(client, _ns(email, admin.id), pw)
            assert client.get(f"/documents/{did}/download").status_code == 200
    finally:
        with app.app_context():
            ClientDocument.query.filter_by(id=did).delete()
            db.session.commit()
        _cleanup(owner.id, other.id, admin.id)


def test_public_form_requires_csrf_token():
    app.config["WTF_CSRF_ENABLED"] = True
    try:
        with app.test_client() as client:
            response = client.post("/contact", data={
                "name": "No Token", "email": "x@example.com",
                "message": "csrf probe",
            }, follow_redirects=False)
            assert response.status_code == 400
    finally:
        app.config["WTF_CSRF_ENABLED"] = False


def test_traversal_filename_cannot_escape_upload_dir(tmp_path, monkeypatch):
    monkeypatch.setitem(app.config, "UPLOAD_FOLDER", str(tmp_path))
    with app.app_context():
        evil = FileStorage(stream=io.BytesIO(b"%PDF-fake"),
                           filename="../../../etc/passwd.pdf",
                           content_type="application/pdf")
        stored, _ = save_private_upload(evil, "documents", "probe",
                                        allowed_document)
        assert stored and "/" not in stored and "\\" not in stored
        assert ".." not in stored
        assert (tmp_path / "documents" / stored).is_file()
        exe = FileStorage(stream=io.BytesIO(b"MZ"),
                          filename="shell.exe", content_type="x-msdownload")
        stored, _ = save_private_upload(exe, "documents", "probe",
                                        allowed_document)
        assert stored == ""
