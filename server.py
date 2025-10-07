from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from pymongo import MongoClient
from uuid import uuid4
import os
from dotenv import load_dotenv
from werkzeug.exceptions import HTTPException

load_dotenv()

app = Flask(__name__, static_folder="static", template_folder="templates")
CORS(app)

MONGODB_URI = os.getenv("MONGODB_URI")
DB_NAME = os.getenv("DB_NAME", "quiz")
PORT = int(os.getenv("PORT", 3000))

if not MONGODB_URI:
    raise RuntimeError("MONGODB_URI is not set.")

client = MongoClient(MONGODB_URI, serverSelectionTimeoutMS=5000)
client.server_info()
db = client[DB_NAME]

def remove_mongo_id(doc):
    if doc: doc.pop("_id", None)
    return doc

@app.errorhandler(Exception)
def handle_exception(e):
    if isinstance(e, HTTPException):
        return jsonify({"message": e.description}), e.code
    return jsonify({"message": "Internal server error", "error": str(e)}), 500

@app.route("/api/users", methods=["GET"])
def get_users():
    users = list(db.users.find({}, {"_id": 0}))
    return jsonify(users)

@app.route("/api/register", methods=["POST"])
def register():
    data = request.json or {}
    user = data.get("user"); passwd = data.get("pass")
    if not user or passwd is None:
        return jsonify({"success": False, "message": "Missing user or pass"}), 400
    if db.users.find_one({"user": user}):
        return jsonify({"success": False, "message": "Tên tài khoản đã tồn tại."}), 409
    new_user = {"id": str(uuid4()), "user": user, "pass": passwd, "role": "student"}
    db.users.insert_one(new_user)
    return jsonify({"success": True, "user": remove_mongo_id(new_user)}), 201

@app.route("/api/login", methods=["POST"])
def login():
    data = request.json or {}
    user = data.get("user"); passwd = data.get("pass")
    found = db.users.find_one({"user": user, "pass": passwd})
    if found:
        return jsonify({"success": True, "user": {"id": found["id"], "user": found["user"], "role": found["role"]}})
    return jsonify({"success": False, "message": "Tên đăng nhập hoặc mật khẩu không đúng."}), 401

@app.route("/", methods=["GET"])
def root():
    return send_from_directory("templates", "index.html")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT)
