from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from pymongo import MongoClient
from uuid import uuid4
import os
from dotenv import load_dotenv
from werkzeug.exceptions import HTTPException
import datetime
import json
from werkzeug.utils import secure_filename

# Định nghĩa thư mục lưu trữ file ảnh
UPLOAD_FOLDER = 'static/uploads'
if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER) 
# Load .env in local; Render provides env vars automatically
load_dotenv()

app = Flask(__name__, static_folder="static", template_folder="templates")
# Allow all origins so frontend on any domain can call this API
CORS(app, resources={r"/*": {"origins": "*"}})

# THÊM DÒNG NÀY: Tăng giới hạn dữ liệu request lên 25MB (25 * 1024 * 1024 bytes)
app.config['MAX_CONTENT_LENGTH'] = 25 * 1024 * 1024 

MONGODB_URI = os.getenv("MONGODB_URI")
DB_NAME = os.getenv("DB_NAME", "quiz")
PORT = int(os.getenv("PORT", 3000))

if not MONGODB_URI:
    raise RuntimeError("MONGODB_URI is not set. Set it in environment variables.")

# Connect to MongoDB
client = MongoClient(MONGODB_URI, serverSelectionTimeoutMS=5000)
try:
    client.server_info()
except Exception as e:
    print("❌ Cannot connect to MongoDB. Check MONGODB_URI. Error:", e)
    raise

db = client[DB_NAME]
print(f"✅ Connected to MongoDB database: {DB_NAME}")

def remove_id(doc):
    if not doc:
        return doc
    doc.pop("_id", None)
    return doc

def remove_id_from_list(docs):
    return [remove_id(d) for d in docs]

# Generic error handler
@app.errorhandler(Exception)
def handle_exception(e):
    if isinstance(e, HTTPException):
        return jsonify({"message": e.description}), e.code
    return jsonify({"message": "Internal server error", "error": str(e)}), 500

# Health
@app.route("/healthz", methods=["GET"])
def health():
    return jsonify({"status": "ok", "db": DB_NAME})

# --------------------- AUTH ---------------------
@app.route("/login", methods=["POST"])
@app.route("/api/login", methods=["POST"])
def login():
    data = request.get_json() or {}
    user = data.get("user")
    passwd = data.get("pass")
    if not user or passwd is None:
        return jsonify({"success": False, "message": "Missing credentials"}), 400
    found = db.users.find_one({"user": user, "pass": passwd})
    if found:
        return jsonify({"success": True, "user": {"id": found.get("id"), "user": found.get("user"), "role": found.get("role")}})
    return jsonify({"success": False, "message": "Tên đăng nhập hoặc mật khẩu không đúng."}), 401

@app.route("/register", methods=["POST"])
@app.route("/api/register", methods=["POST"])
def register():
    data = request.get_json() or {}
    user = data.get("user"); passwd = data.get("pass")
    dob = data.get("dob"); gender = data.get("gender")
    if not user or passwd is None:
        return jsonify({"success": False, "message": "Missing user or pass"}), 400
    if db.users.find_one({"user": user}):
        return jsonify({"success": False, "message": "Tên tài khoản đã tồn tại."}), 409
    new_user = {"id": str(uuid4()), "user": user, "pass": passwd, "dob": dob, "gender": gender, "role": "student"}
    db.users.insert_one(new_user)
    to_return = new_user.copy()
    to_return.pop("_id", None)
    return jsonify({"success": True, "user": to_return}), 201

# --------------------- USERS ---------------------
@app.route("/users", methods=["GET"])
@app.route("/api/users", methods=["GET"])
def get_users():
    docs = list(db.users.find({}, {"_id": 0}))
    return jsonify(docs)

@app.route("/users/<user_id>", methods=["DELETE"])
@app.route("/api/users/<user_id>", methods=["DELETE"])
def delete_user(user_id):
    res = db.users.delete_one({"id": user_id})
    if res.deleted_count > 0:
        return "", 204
    return jsonify({"message": "Người dùng không tìm thấy."}), 404

# --------------------- QUESTIONS ---------------------
@app.route("/questions", methods=["GET"])
@app.route("/api/questions", methods=["GET"])
def list_questions():
    query = {}
    subject = request.args.get("subject")
    level = request.args.get("level")
    # THÊM BỘ LỌC LOẠI CÂU HỎI
    q_type = request.args.get("type") 
    difficulty = request.args.get("difficulty")
    # DÒNG MỚI: LẤY THAM SỐ TÌM KIẾM
    search_keyword = request.args.get("search") 
    if subject: query["subject"] = subject
    if level: query["level"] = level
    # DÒNG QUAN TRỌNG: THÊM BỘ LỌC VÀO TRUY VẤN
    if q_type: query["type"] = q_type
    if difficulty: query["difficulty"] = difficulty
     # THÊM LOGIC TÌM KIẾM BẰNG $regex
    if search_keyword:
        # Tìm kiếm không phân biệt chữ hoa/thường ('i') trong trường 'q'
        query["q"] = {"$regex": search_keyword, "$options": "i"} 
    
    # docs = list(db.questions.find(query, {"_id": 0}))
    docs = list(db.questions.find(query))
    for doc in docs:
        doc['_id'] = str(doc['_id'])
    return jsonify(docs)

@app.route("/questions", methods=["POST"])
@app.route("/api/questions", methods=["POST"])
def create_question():
    # SỬA: Lấy dữ liệu từ request.form (text) và request.files (file)
    data = request.form
    image_file = request.files.get('image')

    # 1. Xử lý File Upload
    image_url = None
    if image_file:
        # Tạo tên file duy nhất và an toàn
        filename = secure_filename(image_file.filename)
        file_ext = os.path.splitext(filename)[1]
        unique_filename = f"{uuid4()}{file_ext}"
        save_path = os.path.join(UPLOAD_FOLDER, unique_filename)
        
        try:
            image_file.save(save_path)
            # URL phải tương ứng với thư mục static đã định nghĩa
            image_url = f"/{UPLOAD_FOLDER}/{unique_filename}"
        except Exception as e:
            return jsonify({"message": f"Lỗi lưu file: {str(e)}"}), 500

    # 2. Parse các trường JSON string (options, answer)
    try:
        options = json.loads(data.get("options", "[]"))
        answer = data.get("answer", "")
    except json.JSONDecodeError:
        return jsonify({"message": "Lỗi định dạng dữ liệu Options hoặc Answer."}), 400

    newq = {
        "id": str(uuid4()),
        "q": data.get("q"),
        "imageUrl": image_url, # Sử dụng URL đã tạo
        "type": data.get("type"),
        "points": int(data.get("points", 1)),
        "subject": data.get("subject"),
        "level": data.get("level"),
        "difficulty": data.get("difficulty", "medium"),
        "options": options,
        "answer": answer
    }
    db.questions.insert_one(newq)
    to_return = newq.copy(); to_return.pop("_id", None)
    return jsonify(to_return), 201

@app.route("/questions/<q_id>", methods=["GET"])
@app.route("/api/questions/<q_id>", methods=["GET"])
def get_question(q_id):
    doc = db.questions.find_one({"id": q_id}, {"_id": 0})
    if not doc: return jsonify({"message": "Câu hỏi không tồn tại."}), 404
    return jsonify(doc)

@app.route("/questions/<q_id>", methods=["PUT"])
@app.route("/api/questions/<q_id>", methods=["PUT"])
def update_question(q_id):
    # SỬA: Lấy dữ liệu từ request.form (text) và request.files (file)
    data = request.form
    image_file = request.files.get('image')
    
    # 1. Chuẩn bị dữ liệu cập nhật
    update_fields = {
        "q": data.get("q"),
        "type": data.get("type"),
        "points": int(data.get("points", 1)),
        "subject": data.get("subject"),
        "level": data.get("level"),
        "difficulty": data.get("difficulty", "medium"),
    }
    
    # 2. Parse các trường JSON string
    try:
        update_fields["options"] = json.loads(data.get("options", "[]"))
        update_fields["answer"] = data.get("answer", "")
    except json.JSONDecodeError:
        return jsonify({"message": "Lỗi định dạng dữ liệu Options hoặc Answer."}), 400

    # 3. Xử lý File Upload Mới
    if image_file:
        # Tạo tên file duy nhất và an toàn
        filename = secure_filename(image_file.filename)
        file_ext = os.path.splitext(filename)[1]
        unique_filename = f"{uuid4()}{file_ext}"
        save_path = os.path.join(UPLOAD_FOLDER, unique_filename)
        
        try:
            image_file.save(save_path)
            update_fields["imageUrl"] = f"/{UPLOAD_FOLDER}/{unique_filename}"
        except Exception as e:
            return jsonify({"message": f"Lỗi lưu file: {str(e)}"}), 500
    
    # 4. Cập nhật vào MongoDB
    res = db.questions.update_one({"id": q_id}, {"$set": update_fields})
    
    if res.matched_count > 0:
        updated = db.questions.find_one({"id": q_id}, {"_id": 0})
        return jsonify(updated)
    
    return jsonify({"message": "Câu hỏi không tồn tại."}), 404

@app.route("/questions/<q_id>", methods=["DELETE"])
@app.route("/api/questions/<q_id>", methods=["DELETE"])
def delete_question(q_id):
    res = db.questions.delete_one({"id": q_id})
    if res.deleted_count > 0:
        return "", 204
    return jsonify({"message": "Câu hỏi không tìm thấy."}), 404

# --------------------- TESTS ---------------------
@app.route('/test.html')
def serve_test_html():
    # Sử dụng os.path.dirname(__file__) để lấy thư mục của file server.py
    # và phục vụ file test.html từ thư mục đó.
    current_dir = os.path.dirname(os.path.abspath(__file__))
    return send_from_directory(current_dir, 'test.html') 

@app.route("/tests", methods=["GET"])
@app.route("/api/tests", methods=["GET"])
def list_tests():
    query = {}
    subject = request.args.get("subject")
    level = request.args.get("level")
    # THÊM: Lấy tham số lọc theo ngày tạo (Lớn hơn hoặc bằng)
    createdAtGte = request.args.get("createdAtGte") 

    if subject: 
        query["subject"] = subject
    if level: 
        query["level"] = level
    
    if createdAtGte:
        # Lọc theo ngày tạo Lớn hơn hoặc bằng (Frontend gửi YYYY-MM-DD)
        # So sánh chuỗi ISO-8601 (YYYY-MM-DDTHH:MM:SS...) với YYYY-MM-DD bằng $gte hoạt động.
        query["createdAt"] = {"$gte": createdAtGte}

    docs = list(db.tests.find(query, {"_id": 0}))
    return jsonify(docs)

@app.route("/tests/<test_id>", methods=["GET"])
@app.route("/api/tests/<test_id>", methods=["GET"])
def get_test(test_id):
    # Thử tìm kiếm cả bằng trường 'id' (UUID chuỗi) và 'testId' (nếu có)
    doc = db.tests.find_one({
        "$or": [
            {"id": test_id},  # Tìm kiếm theo trường ID chính (UUID)
            {"_id": test_id}  # Phòng trường hợp _id được lưu dưới dạng chuỗi
        ]
    }, {"_id": 0})
    
    if not doc: 
        return jsonify({"message": "Bài kiểm tra không tồn tại."}), 404
        
    return jsonify(doc)

@app.route("/tests", methods=["POST"])
@app.route("/api/tests", methods=["POST"])
def create_test():
    data = request.get_json() or {}
    newt = {
        "id": str(uuid4()),
        "name": data.get("name"),
        "time": data.get("time"),
        "subject": data.get("subject"),
        "level": data.get("level"),
        "questions": data.get("questions", []),
        "teacherId": data.get("teacherId"),
        # THÊM: Ngày tạo (ISO format)
        "createdAt": datetime.datetime.utcnow().isoformat()
    }
    db.tests.insert_one(newt)
    to_return = newt.copy(); to_return.pop("_id", None)
    return jsonify(to_return), 201


@app.route("/tests/auto", methods=["POST"])
@app.route("/api/tests/auto", methods=["POST"])
def create_test_auto():
    data = request.get_json() or {}
    name = data.get("name", "Bài kiểm tra ngẫu nhiên")
    subject = data.get("subject", "")
    level = data.get("level", "")
    total = int(data.get("total", data.get("count", 10)))
    time = int(data.get("time", 30))
    dist = data.get("dist", {"easy": 0, "medium": 0, "hard": 0})

    # helper to pick questions by difficulty
    def pick(diff, count):
        q = {"difficulty": diff}
        if subject:
            q["subject"] = subject
        if level:
            q["level"] = level
        all_q = list(db.questions.find(q, {"_id": 0}))
        import random
        random.shuffle(all_q)
        return all_q[:count]

    selected = []
    try:
        selected += pick("easy", int(dist.get("easy", 0)))
        selected += pick("medium", int(dist.get("medium", 0)))
        selected += pick("hard", int(dist.get("hard", 0)))
    except Exception:
        # fallback: ignore dist parse errors
        pass

    # fill remaining if not enough
    if len(selected) < total:
        remain = total - len(selected)
        candidates = list(db.questions.find({}, {"_id": 0}))
        import random
        random.shuffle(candidates)
        # avoid duplicates by id if possible
        existing_ids = {q.get("id") for q in selected}
        added = []
        for c in candidates:
            if c.get("id") in existing_ids:
                continue
            added.append(c)
            existing_ids.add(c.get("id"))
            if len(added) >= remain:
                break
        selected += added

    selected = selected[:total]
    question_ids = [q.get("id") for q in selected if q.get("id")]

    newt = {
        "id": str(uuid4()),
        "name": name,
        "time": time,
        "subject": subject,
        "level": level,
        "questions": question_ids,
        "count": len(question_ids),
        "teacherId": data.get("teacherId"),
        # THÊM: Ngày tạo (ISO format)
        "createdAt": datetime.datetime.utcnow().isoformat()
    }
    db.tests.insert_one(newt)
    to_return = newt.copy(); to_return.pop("_id", None)
    return jsonify(to_return), 201

@app.route("/tests/<test_id>", methods=["PUT"])
@app.route("/api/tests/<test_id>", methods=["PUT"])
def update_test(test_id):
    data = request.get_json() or {}
    data.pop("_id", None)
    res = db.tests.update_one({"id": test_id}, {"$set": data})
    if res.matched_count > 0:
        updated = db.tests.find_one({"id": test_id}, {"_id": 0})
        return jsonify(updated)
    return jsonify({"message": "Bài kiểm tra không tồn tại."}), 404

@app.route("/tests/<test_id>", methods=["DELETE"])
@app.route("/api/tests/<test_id>", methods=["DELETE"])
def delete_test(test_id):
    res = db.tests.delete_one({"id": test_id})
    if res.deleted_count > 0:
        return "", 204
    return jsonify({"message": "Bài kiểm tra không tìm thấy."}), 404

# --------------------- ASSIGNS ---------------------
@app.route("/assigns", methods=["GET"])
@app.route("/api/assigns", methods=["GET"])
def list_assigns():
    studentId = request.args.get("studentId")
    
    pipeline = []
    
    # 1. Lọc theo studentId (Nếu có)
    if studentId: 
        pipeline.append({"$match": {"studentId": studentId}})

    # 2. Bước Lookup (JOIN): Kết nối assigns với tests
    pipeline.append({
        "$lookup": {
            "from": "tests",         # Tên bộ sưu tập đề thi
            "localField": "testId",  # Trường ID đề thi trong bộ sưu tập 'assigns'
            "foreignField": "id",    # Trường ID đề thi trong bộ sưu tập 'tests'
            "as": "testInfo"         # Đặt kết quả vào trường 'testInfo'
        }
    })

    # 3. Bước Unwind: Biến mảng 'testInfo' thành đối tượng
    pipeline.append({"$unwind": {"path": "$testInfo", "preserveNullAndEmptyArrays": True}})

    # 4. Bước Projection: Định hình lại và chọn các trường cần thiết
    pipeline.append({
        "$project": {
            "_id": 0,
            "id": "$id",
            "testId": "$testId",
            "studentId": "$studentId",
            "deadline": "$deadline",
            "status": "$status",
            "timeAssigned": "$timeAssigned",
            
            # Lấy tên đề thi (Trường 'name' từ 'tests')
            "testName": "$testInfo.name", 
            
            # Lấy môn học (Trường 'subject' từ 'tests')
            "subject": "$testInfo.subject", 
            
            # Lấy thời gian làm bài (Trường 'time' từ 'tests')
            "time": "$testInfo.time" 
        }
    })

    # 5. Thực thi Aggregation và trả về kết quả
    # Sử dụng db.assigns vì đây là bộ sưu tập khởi đầu của pipeline
    docs = list(db.assigns.aggregate(pipeline)) 
    return jsonify(docs)

@app.route("/assigns", methods=["POST"])
@app.route("/api/assigns", methods=["POST"])
def create_assign():
    data = request.get_json() or {}
    newa = {
        "id": str(uuid4()),
        "testId": data.get("testId"),
        "studentId": data.get("studentId"),
        "deadline": data.get("deadline"),
        "status": data.get("status"),
        "timeAssigned": data.get("timeAssigned") or datetime.datetime.utcnow().isoformat()
    }
    db.assigns.insert_one(newa)
    to_return = newa.copy(); to_return.pop("_id", None)
    return jsonify(to_return), 201

@app.route("/api/assign-test", methods=["POST"])
def alias_assign_test():
    return create_assign()

@app.route("/api/assign-multiple", methods=["POST"])
def assign_multiple():
    data = request.get_json() or {}
    test_id = data.get("testId")
    students = data.get("students", [])
    if not test_id or not students:
        return jsonify({"success": False, "message": "Thiếu testId hoặc danh sách học sinh"}), 400

    created = []
    for sid in students:
        newa = {
            "id": str(uuid4()),
            "testId": test_id,
            "studentId": sid,
            "deadline": data.get("deadline"),
            "status": "assigned",
            "timeAssigned": datetime.datetime.utcnow().isoformat()
        }
        db.assigns.insert_one(newa)
        newa.pop("_id", None)
        created.append(newa)

    return jsonify({"success": True, "count": len(created), "assigns": created}), 201

# --------------------- RESULTS ---------------------
@app.route("/results", methods=["GET"])
@app.route("/api/results", methods=["GET"])
def list_results():
    query = {}
    studentId = request.args.get("studentId")
    if studentId: query["studentId"] = studentId
    docs = list(db.results.find(query, {"_id": 0}))
    return jsonify(docs)

@app.route("/results", methods=["POST"])
@app.route("/api/results", methods=["POST"])
def create_result():
    data = request.get_json() or {}
    newr = {"id": str(uuid4()), **data, "submittedAt": datetime.datetime.utcnow().isoformat()}
    db.results.insert_one(newr)
    to_return = newr.copy(); to_return.pop("_id", None)
    return jsonify(to_return), 201

@app.route("/results/<result_id>", methods=["GET"])
@app.route("/api/results/<result_id>", methods=["GET"])
def get_result(result_id):
    doc = db.results.find_one({"id": result_id}, {"_id": 0})
    if not doc: return jsonify({"message": "Kết quả không tìm thấy."}), 404
    return jsonify(doc)

# Serve frontend files (unchanged)
@app.route("/", methods=["GET"])
def index():
    try:
        return send_from_directory(".", "index.html")
    except Exception:
        return jsonify({"message": "Index not found"}), 404

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT)
