import os
import sqlite3
import hashlib
import json
import mimetypes
from datetime import datetime
from functools import wraps
from flask import Flask, request, jsonify, session, send_file, abort
import fitz  # PyMuPDF
import pytesseract
from PIL import Image
import io
import requests

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "docvault-secret-2024")

UPLOAD_FOLDER = "uploads"
DB_PATH = "docvault.db"
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB
ALLOWED_EXT = {".pdf", ".png", ".jpg", ".jpeg", ".docx", ".txt", ".xlsx", ".csv"}
OLLAMA_URL = "http://localhost:11434/api/generate"

os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# ─── DB ───────────────────────────────────────────────────────────────────────
def get_db():
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    return db

def init_db():
    db = get_db()
    db.executescript("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        password TEXT NOT NULL,
        role TEXT DEFAULT 'viewer',
        created_at TEXT DEFAULT (datetime('now'))
    );
    CREATE TABLE IF NOT EXISTS folders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        parent_id INTEGER,
        owner_id INTEGER,
        created_at TEXT DEFAULT (datetime('now')),
        FOREIGN KEY (owner_id) REFERENCES users(id)
    );
    CREATE TABLE IF NOT EXISTS documents (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        filename TEXT NOT NULL,
        original_name TEXT NOT NULL,
        file_type TEXT,
        file_size INTEGER,
        folder_id INTEGER,
        owner_id INTEGER,
        extracted_text TEXT,
        ai_summary TEXT,
        tags TEXT DEFAULT '[]',
        created_at TEXT DEFAULT (datetime('now')),
        FOREIGN KEY (folder_id) REFERENCES folders(id),
        FOREIGN KEY (owner_id) REFERENCES users(id)
    );
    CREATE TABLE IF NOT EXISTS permissions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        document_id INTEGER,
        user_id INTEGER,
        access_level TEXT DEFAULT 'read',
        FOREIGN KEY (document_id) REFERENCES documents(id),
        FOREIGN KEY (user_id) REFERENCES users(id)
    );
    """)
    # Default admin
    pw = hashlib.sha256("admin123".encode()).hexdigest()
    try:
        db.execute("INSERT INTO users (username, password, role) VALUES (?, ?, ?)", ("admin", pw, "admin"))
    except:
        pass
    db.commit()
    db.close()

init_db()

# ─── Auth helpers ─────────────────────────────────────────────────────────────
def hash_pw(pw): return hashlib.sha256(pw.encode()).hexdigest()

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            return jsonify({"error": "Unauthorized"}), 401
        return f(*args, **kwargs)
    return decorated

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if session.get("role") != "admin":
            return jsonify({"error": "Forbidden"}), 403
        return f(*args, **kwargs)
    return decorated

# ─── OCR / Text extraction ────────────────────────────────────────────────────
def extract_text(filepath, ext):
    try:
        if ext == ".pdf":
            doc = fitz.open(filepath)
            text = ""
            for page in doc:
                t = page.get_text()
                if t.strip():
                    text += t
                else:
                    pix = page.get_pixmap(dpi=150)
                    img = Image.open(io.BytesIO(pix.tobytes("png")))
                    text += pytesseract.image_to_string(img, lang="spa+eng")
            return text.strip()
        elif ext in {".png", ".jpg", ".jpeg"}:
            img = Image.open(filepath)
            return pytesseract.image_to_string(img, lang="spa+eng").strip()
        elif ext == ".txt":
            with open(filepath, "r", errors="ignore") as f:
                return f.read()
        elif ext == ".csv":
            with open(filepath, "r", errors="ignore") as f:
                return f.read()
    except Exception as e:
        return f"[Error extrayendo texto: {str(e)}]"
    return ""

def ai_summarize(text):
    if not text or len(text.strip()) < 50:
        return ""
    try:
        prompt = f"""Eres un asistente de gestión documental empresarial. Analiza el siguiente texto y devuelve un JSON con estos campos:
- summary: resumen ejecutivo en 2-3 frases
- document_type: tipo de documento (factura, contrato, informe, presupuesto, acta, otro)
- key_info: lista de 3-5 datos clave extraídos
- suggested_tags: lista de 3-5 etiquetas relevantes

Texto:
{text[:3000]}

Responde SOLO con JSON válido, sin markdown."""
        res = requests.post(OLLAMA_URL, json={
            "model": "llama3",
            "prompt": prompt,
            "stream": False
        }, timeout=30)
        if res.ok:
            raw = res.json().get("response", "")
            # clean possible markdown
            raw = raw.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
            return raw
    except:
        pass
    return ""

# ─── Routes: Auth ─────────────────────────────────────────────────────────────
@app.route("/api/auth/login", methods=["POST"])
def login():
    data = request.json
    db = get_db()
    user = db.execute("SELECT * FROM users WHERE username=? AND password=?",
                      (data["username"], hash_pw(data["password"]))).fetchone()
    db.close()
    if not user:
        return jsonify({"error": "Credenciales incorrectas"}), 401
    session["user_id"] = user["id"]
    session["username"] = user["username"]
    session["role"] = user["role"]
    return jsonify({"id": user["id"], "username": user["username"], "role": user["role"]})

@app.route("/api/auth/logout", methods=["POST"])
def logout():
    session.clear()
    return jsonify({"ok": True})

@app.route("/api/auth/me")
def me():
    if "user_id" not in session:
        return jsonify({"error": "Not logged in"}), 401
    return jsonify({"id": session["user_id"], "username": session["username"], "role": session["role"]})

@app.route("/api/auth/register", methods=["POST"])
@login_required
@admin_required
def register():
    data = request.json
    db = get_db()
    try:
        db.execute("INSERT INTO users (username, password, role) VALUES (?,?,?)",
                   (data["username"], hash_pw(data["password"]), data.get("role","viewer")))
        db.commit()
    except:
        db.close()
        return jsonify({"error": "Usuario ya existe"}), 400
    db.close()
    return jsonify({"ok": True})

# ─── Routes: Users ────────────────────────────────────────────────────────────
@app.route("/api/users")
@login_required
@admin_required
def list_users():
    db = get_db()
    users = db.execute("SELECT id, username, role, created_at FROM users ORDER BY id").fetchall()
    db.close()
    return jsonify([dict(u) for u in users])

@app.route("/api/users/<int:uid>", methods=["DELETE"])
@login_required
@admin_required
def delete_user(uid):
    if uid == session["user_id"]:
        return jsonify({"error": "No puedes eliminarte a ti mismo"}), 400
    db = get_db()
    db.execute("DELETE FROM users WHERE id=?", (uid,))
    db.commit()
    db.close()
    return jsonify({"ok": True})

# ─── Routes: Folders ─────────────────────────────────────────────────────────
@app.route("/api/folders")
@login_required
def list_folders():
    db = get_db()
    folders = db.execute("""
        SELECT f.*, u.username as owner_name,
               (SELECT COUNT(*) FROM documents d WHERE d.folder_id = f.id) as doc_count
        FROM folders f JOIN users u ON f.owner_id = u.id
        ORDER BY f.name
    """).fetchall()
    db.close()
    return jsonify([dict(f) for f in folders])

@app.route("/api/folders", methods=["POST"])
@login_required
def create_folder():
    data = request.json
    db = get_db()
    cur = db.execute("INSERT INTO folders (name, parent_id, owner_id) VALUES (?,?,?)",
                     (data["name"], data.get("parent_id"), session["user_id"]))
    db.commit()
    fid = cur.lastrowid
    db.close()
    return jsonify({"id": fid, "name": data["name"]})

@app.route("/api/folders/<int:fid>", methods=["DELETE"])
@login_required
def delete_folder(fid):
    db = get_db()
    db.execute("UPDATE documents SET folder_id = NULL WHERE folder_id=?", (fid,))
    db.execute("DELETE FROM folders WHERE id=?", (fid,))
    db.commit()
    db.close()
    return jsonify({"ok": True})

# ─── Routes: Documents ───────────────────────────────────────────────────────
@app.route("/api/documents")
@login_required
def list_documents():
    folder_id = request.args.get("folder_id")
    search = request.args.get("q", "").strip()
    db = get_db()
    query = """
        SELECT d.*, u.username as owner_name, f.name as folder_name
        FROM documents d
        JOIN users u ON d.owner_id = u.id
        LEFT JOIN folders f ON d.folder_id = f.id
        WHERE 1=1
    """
    params = []
    if folder_id:
        query += " AND d.folder_id = ?"
        params.append(folder_id)
    if search:
        query += " AND (d.original_name LIKE ? OR d.extracted_text LIKE ? OR d.tags LIKE ?)"
        params += [f"%{search}%", f"%{search}%", f"%{search}%"]
    query += " ORDER BY d.created_at DESC"
    docs = db.execute(query, params).fetchall()
    db.close()
    return jsonify([dict(d) for d in docs])

@app.route("/api/documents/upload", methods=["POST"])
@login_required
def upload_document():
    if "file" not in request.files:
        return jsonify({"error": "No file"}), 400
    file = request.files["file"]
    folder_id = request.form.get("folder_id") or None
    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in ALLOWED_EXT:
        return jsonify({"error": f"Tipo de archivo no permitido: {ext}"}), 400

    safe_name = f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{hashlib.md5(file.filename.encode()).hexdigest()[:6]}{ext}"
    filepath = os.path.join(UPLOAD_FOLDER, safe_name)
    file.save(filepath)
    size = os.path.getsize(filepath)

    # Extract text
    extracted = extract_text(filepath, ext)

    # AI summary
    ai_result = ai_summarize(extracted)
    suggested_tags = "[]"
    if ai_result:
        try:
            parsed = json.loads(ai_result)
            suggested_tags = json.dumps(parsed.get("suggested_tags", []))
        except:
            pass

    db = get_db()
    cur = db.execute("""
        INSERT INTO documents (filename, original_name, file_type, file_size, folder_id, owner_id, extracted_text, ai_summary, tags)
        VALUES (?,?,?,?,?,?,?,?,?)
    """, (safe_name, file.filename, ext, size, folder_id, session["user_id"], extracted, ai_result, suggested_tags))
    db.commit()
    doc_id = cur.lastrowid
    db.close()
    return jsonify({"id": doc_id, "name": file.filename, "ok": True})

@app.route("/api/documents/<int:doc_id>")
@login_required
def get_document(doc_id):
    db = get_db()
    doc = db.execute("""
        SELECT d.*, u.username as owner_name, f.name as folder_name
        FROM documents d JOIN users u ON d.owner_id = u.id
        LEFT JOIN folders f ON d.folder_id = f.id
        WHERE d.id = ?
    """, (doc_id,)).fetchone()
    db.close()
    if not doc:
        abort(404)
    return jsonify(dict(doc))

@app.route("/api/documents/<int:doc_id>", methods=["PATCH"])
@login_required
def update_document(doc_id):
    data = request.json
    db = get_db()
    if "folder_id" in data:
        db.execute("UPDATE documents SET folder_id=? WHERE id=?", (data["folder_id"], doc_id))
    if "tags" in data:
        db.execute("UPDATE documents SET tags=? WHERE id=?", (json.dumps(data["tags"]), doc_id))
    db.commit()
    db.close()
    return jsonify({"ok": True})

@app.route("/api/documents/<int:doc_id>", methods=["DELETE"])
@login_required
def delete_document(doc_id):
    db = get_db()
    doc = db.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
    if doc:
        try: os.remove(os.path.join(UPLOAD_FOLDER, doc["filename"]))
        except: pass
        db.execute("DELETE FROM documents WHERE id=?", (doc_id,))
        db.commit()
    db.close()
    return jsonify({"ok": True})

@app.route("/api/documents/<int:doc_id>/download")
@login_required
def download_document(doc_id):
    db = get_db()
    doc = db.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
    db.close()
    if not doc:
        abort(404)
    filepath = os.path.join(UPLOAD_FOLDER, doc["filename"])
    return send_file(filepath, download_name=doc["original_name"], as_attachment=True)

@app.route("/api/documents/<int:doc_id>/preview")
@login_required
def preview_document(doc_id):
    db = get_db()
    doc = db.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
    db.close()
    if not doc:
        abort(404)
    filepath = os.path.join(UPLOAD_FOLDER, doc["filename"])
    mime = mimetypes.guess_type(doc["original_name"])[0] or "application/octet-stream"
    return send_file(filepath, mimetype=mime)

@app.route("/api/documents/<int:doc_id>/ai-analyze", methods=["POST"])
@login_required
def ai_analyze(doc_id):
    db = get_db()
    doc = db.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
    if not doc:
        db.close()
        abort(404)
    text = doc["extracted_text"] or ""
    result = ai_summarize(text)
    if result:
        db.execute("UPDATE documents SET ai_summary=? WHERE id=?", (result, doc_id))
        try:
            parsed = json.loads(result)
            tags = json.dumps(parsed.get("suggested_tags", []))
            db.execute("UPDATE documents SET tags=? WHERE id=?", (tags, doc_id))
        except:
            pass
        db.commit()
    db.close()
    return jsonify({"result": result})

# ─── Stats ────────────────────────────────────────────────────────────────────
@app.route("/api/stats")
@login_required
def stats():
    db = get_db()
    total_docs = db.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
    total_folders = db.execute("SELECT COUNT(*) FROM folders").fetchone()[0]
    total_users = db.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    total_size = db.execute("SELECT COALESCE(SUM(file_size),0) FROM documents").fetchone()[0]
    recent = db.execute("""
        SELECT d.original_name, d.created_at, u.username
        FROM documents d JOIN users u ON d.owner_id=u.id
        ORDER BY d.created_at DESC LIMIT 5
    """).fetchall()
    by_type = db.execute("""
        SELECT file_type, COUNT(*) as count FROM documents GROUP BY file_type
    """).fetchall()
    db.close()
    return jsonify({
        "total_docs": total_docs,
        "total_folders": total_folders,
        "total_users": total_users,
        "total_size": total_size,
        "recent": [dict(r) for r in recent],
        "by_type": [dict(r) for r in by_type]
    })

# ─── Serve frontend ───────────────────────────────────────────────────────────
@app.route("/")
@app.route("/<path:path>")
def index(path=""):
    return send_file("index.html")

if __name__ == "__main__":
    app.run(debug=True, port=5000)
