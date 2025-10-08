```python
# server.py
# Flask backend with MongoDB Atlas support.
# - Reads MONGODB_URI and DB_NAME from environment (.env for local).
# - CRUD for users, questions, tests.
# - /api/assign-test: create per-student assignment copies (assignments collection).
# - /api/assignments and /api/results endpoints for viewing/updating assignments.

from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from pymongo import MongoClient
from uuid import uuid4
import os, datetime
from dotenv import load_dotenv
from werkzeug.exceptions import HTTPException

load_dotenv()

app = Flask(__name__, static_folder="static", template_folder="templates")
CORS(app, resources={r"/*": {"origins": "*"}})

MONGODB_URI = os.getenv("MONGODB_URI")
DB_NAME = os.getenv("DB_NAME", "quiz")
PORT = int(os.getenv("PORT", 3000))

if not MONGODB_URI:
    raise RuntimeError("MONGODB_URI is not set. Add it to environment variables or .env")

client = MongoClient(MONGODB_URI, serverSelectionTimeoutMS=5000)
try:
    client.server_info()
except Exception as e:
    print("❌ Cannot connect to MongoDB. Check MONGODB_URI. Error:", e)
    raise

db = client[DB_NAME]
print(f"✅ Connected to MongoDB database: {DB_NAME}")


def strip_id(doc):
    if not doc: 
        return doc
    doc.pop("_id", None)
    return doc

def strip_list(docs):
    return [strip_id(d) for d in docs]


@app.errorhandler(Exception)
def handle_exception(e):
    if isinstance(e, HTTPException):
        return jsonify({"message": e.description}), e.code
    return jsonify({"message": "Internal server error", "error": str(e)}), 500


# Health
@app.route("/healthz", methods=["GET"])
def health():
    return jsonify({"status": "ok", "db": DB_NAME})


# -------------------- USERS (students + teachers) --------------------
@app.route("/api/users", methods=["GET"])
def get_users():
    users = list(db.users.find({}, {"_id": 0}))
    return jsonify(users)

@app.route("/api/users", methods=["POST"])
def create_user():
    data = request.json or {}
    if not data.get("user") or data.get("pass") is None:
        return jsonify({"message": "user and pass required"}), 400
    if db.users.find_one({"user": data["user"]}):
        return jsonify({"message": "user exists"}), 409
    new_user = {
        "id": str(uuid4()),
        "user": data["user"],
        "pass": data["pass"],
        "role": data.get("role", "student"),
        "dob": data.get("dob"),
        "gender": data.get("gender")
    }
    db.users.insert_one(new_user)
    strip_id(new_user)
    return jsonify(new_user), 201

@app.route("/api/users/<user_id>", methods=["PUT"])
def update_user(user_id):
    data = request.json or {}
    data.pop("_id", None)
    db.users.update_one({"id": user_id}, {"$set": data})
    updated = db.users.find_one({"id": user_id}, {"_id": 0})
    return jsonify(updated)

@app.route("/api/users/<user_id>", methods=["DELETE"])
def delete_user(user_id):
    res = db.users.delete_one({"id": user_id})
    if res.deleted_count:
        return "", 204
    return jsonify({"message": "not found"}), 404


# -------------------- LOGIN (both /login and /api/login) --------------------
@app.route("/login", methods=["POST"])
@app.route("/api/login", methods=["POST"])
def login():
    data = request.json or {}
    user = data.get("user")
    passwd = data.get("pass")
    if not user or passwd is None:
        return jsonify({"success": False, "message": "Missing credentials"}), 400
    found = db.users.find_one({"user": user, "pass": passwd}, {"_id": 0})
    if found:
        return jsonify({"success": True, "user": found})
    return jsonify({"success": False, "message": "Tên đăng nhập hoặc mật khẩu không đúng."}), 401


# -------------------- QUESTIONS --------------------
@app.route("/api/questions", methods=["GET"])
def list_questions():
    q = {}
    subject = request.args.get("subject")
    level = request.args.get("level")
    if subject: q["subject"] = subject
    if level: q["level"] = level
    docs = list(db.questions.find(q, {"_id": 0}))
    return jsonify(docs)

@app.route("/api/questions", methods=["POST"])
def create_question():
    data = request.json or {}
    newq = {
        "id": str(uuid4()),
        "q": data.get("q"),
        "imageUrl": data.get("imageUrl"),
        "type": data.get("type"),
        "points": data.get("points", 1),
        "subject": data.get("subject"),
        "level": data.get("level"),
        "options": data.get("options", [])
    }
    db.questions.insert_one(newq)
    strip_id(newq)
    return jsonify(newq), 201

@app.route("/api/questions/<q_id>", methods=["PUT"])
def update_question(q_id):
    data = request.json or {}
    data.pop("_id", None)
    db.questions.update_one({"id": q_id}, {"$set": data})
    updated = db.questions.find_one({"id": q_id}, {"_id": 0})
    return jsonify(updated)

@app.route("/api/questions/<q_id>", methods=["GET"])
def get_question(q_id):
    doc = db.questions.find_one({"id": q_id}, {"_id": 0})
    if not doc: return jsonify({"message": "not found"}), 404
    return jsonify(doc)

@app.route("/api/questions/<q_id>", methods=["DELETE"])
def delete_question(q_id):
    res = db.questions.delete_one({"id": q_id})
    if res.deleted_count:
        return "", 204
    return jsonify({"message": "not found"}), 404


# -------------------- TESTS --------------------
@app.route("/api/tests", methods=["GET"])
def list_tests():
    docs = list(db.tests.find({}, {"_id": 0}))
    return jsonify(docs)

@app.route("/api/tests", methods=["POST"])
def create_test():
    data = request.json or {}
    newt = {
        "id": str(uuid4()),
        "name": data.get("name"),
        "time": data.get("time"),
        "subject": data.get("subject"),
        "level": data.get("level"),
        "questions": data.get("questions", []),  # array of question ids or question objects
        "teacherId": data.get("teacherId")
    }
    db.tests.insert_one(newt)
    strip_id(newt)
    return jsonify(newt), 201

@app.route("/api/tests/<t_id>", methods=["PUT"])
def update_test(t_id):
    data = request.json or {}
    data.pop("_id", None)
    db.tests.update_one({"id": t_id}, {"$set": data})
    updated = db.tests.find_one({"id": t_id}, {"_id": 0})
    return jsonify(updated)

@app.route("/api/tests/<t_id>", methods=["GET"])
def get_test(t_id):
    doc = db.tests.find_one({"id": t_id}, {"_id": 0})
    if not doc: return jsonify({"message": "not found"}), 404
    return jsonify(doc)

@app.route("/api/tests/<t_id>", methods=["DELETE"])
def delete_test(t_id):
    res = db.tests.delete_one({"id": t_id})
    if res.deleted_count:
        return "", 204
    return jsonify({"message": "not found"}), 404


# -------------------- ASSIGN TEST (create per-student assignment copies) --------------------
@app.route("/api/assign-test", methods=["POST"])
def assign_test():
    """
    Expected body:
    {
      "testId": "...",
      "studentIds": ["id1","id2", ...]
    }
    Creates one assignment document per student in collection `assignments`.
    """
    data = request.json or {}
    testId = data.get("testId")
    studentIds = data.get("studentIds", [])
    if not testId or not studentIds:
        return jsonify({"success": False, "message": "testId and studentIds required"}), 400

    test = db.tests.find_one({"id": testId}, {"_id": 0})
    if not test:
        return jsonify({"success": False, "message": "test not found"}), 404

    # Resolve questions: if test.questions contains ids, fetch them; else assume embedded objects
    questions = []
    if test.get("questions"):
        # If elements look like simple ids, fetch them
        if all(isinstance(x, str) for x in test["questions"]):
            qs = list(db.questions.find({"id": {"$in": test["questions"]}}, {"_id": 0}))
            # preserve ordering by ids in test["questions"]
            qmap = {q["id"]: q for q in qs}
            questions = [qmap[qid] for qid in test["questions"] if qid in qmap]
        else:
            # assume questions already embedded
            questions = test["questions"]

    assigned_docs = []
    now = datetime.datetime.utcnow().isoformat()
    for sid in studentIds:
        assignment = {
            "id": str(uuid4()),
            "testId": test["id"],
            "testName": test.get("name"),
            "studentId": sid,
            "questions": questions,
            "status": "not_started",
            "score": None,
            "answers": [],
            "assignedAt": now,
            "startedAt": None,
            "submittedAt": None
        }
        assigned_docs.append(assignment)

    if assigned_docs:
        db.assignments.insert_many(assigned_docs)
    return jsonify({"success": True, "count": len(assigned_docs)}), 201


# -------------------- ASSIGNMENTS & RESULTS --------------------
@app.route("/api/assignments", methods=["GET"])
def list_assignments():
    q = {}
    studentId = request.args.get("studentId")
    testId = request.args.get("testId")
    if studentId: q["studentId"] = studentId
    if testId: q["testId"] = testId
    docs = list(db.assignments.find(q, {"_id": 0}))
    return jsonify(docs)

@app.route("/api/assignments/<assign_id>", methods=["GET"])
def get_assignment(assign_id):
    doc = db.assignments.find_one({"id": assign_id}, {"_id": 0})
    if not doc: return jsonify({"message": "not found"}), 404
    return jsonify(doc)

@app.route("/api/assignments/<assign_id>", methods=["PUT"])
def update_assignment(assign_id):
    """
    Update assignment (e.g., submit answers, set score).
    Body may include: status, answers (array), score, submittedAt
    """
    data = request.json or {}
    data.pop("_id", None)
    if data.get("status") == "in_progress":
        data["startedAt"] = datetime.datetime.utcnow().isoformat()
    if data.get("status") == "completed":
        data["submittedAt"] = datetime.datetime.utcnow().isoformat()
    db.assignments.update_one({"id": assign_id}, {"$set": data})
    updated = db.assignments.find_one({"id": assign_id}, {"_id": 0})
    return jsonify(updated)


# -------------------- Simple submit endpoint for students (optional) --------------------
@app.route("/api/submit-assignment/<assign_id>", methods=["POST"])
def submit_assignment(assign_id):
    data = request.json or {}
    answers = data.get("answers", [])
    # naive scoring: if assignment.questions contains options with .correct boolean,
    # compute simple score
    assignment = db.assignments.find_one({"id": assign_id}, {"_id": 0})
    if not assignment:
        return jsonify({"message": "assignment not found"}), 404
    total = 0
    score = 0
    for q, a in zip(assignment.get("questions", []), answers):
        total += q.get("points", 1)
        # find correct options if possible
        opts = q.get("options", [])
        # if multiple correct, assume a is array; here simple: match first correct string
        corrects = [o["text"] for o in opts if o.get("correct")]
        if isinstance(a, str) and a in corrects:
            score += q.get("points", 1)
    now = datetime.datetime.utcnow().isoformat()
    db.assignments.update_one({"id": assign_id}, {"$set": {"answers": answers, "status": "completed", "score": score, "submittedAt": now}})
    return jsonify({"success": True, "score": score})


# Serve frontend files (index or static)
@app.route("/", methods=["GET"])
def index():
    # prefer templates/index.html if exists, else root index.html
    try:
        return send_from_directory("templates", "index.html")
    except Exception:
        return send_from_directory(".", "index.html")

@app.route("/static/<path:path>")
def send_static(path):
    return send_from_directory("static", path)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT)
```
