from bson.objectid import ObjectId
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from pymongo import MongoClient
from uuid import uuid4
import os
from dotenv import load_dotenv
from werkzeug.exceptions import HTTPException
from datetime import datetime, timedelta, timezone
import json
from werkzeug.utils import secure_filename
from gridfs import GridFS

# Load .env in local; Render provides env vars automatically
load_dotenv()

app = Flask(__name__)
# Allow all origins so frontend on any domain can call this API
CORS(app, resources={r"/*": {"origins": "*"}})

def calculate_question_counts(question_ids, db):
    """Tính toán số câu MC và Essay từ danh sách ID câu hỏi."""
    if not question_ids:
        return 0, 0

    object_ids = []
    uuid_strings = []
    for qid_str in question_ids:
        try:
            object_ids.append(ObjectId(qid_str))
        except Exception:
            uuid_strings.append(qid_str)

    # Tạo query $or
    or_clauses = []
    if object_ids:
        or_clauses.append({"_id": {"$in": object_ids}})
    if uuid_strings:
        or_clauses.append({"id": {"$in": uuid_strings}})

    # Chỉ truy vấn trường 'type'
    question_types = list(db.questions.find(
        {"$or": or_clauses} if or_clauses else {},
        {"type": 1}
    ))

    mc_count = 0
    essay_count = 0

    for q in question_types:
        q_type = q.get("type", "").lower()
        if q_type == "mc":
            mc_count += 1
        elif q_type == "essay":
            essay_count += 1
        # Nếu không có type: cố gắng xác định dựa trên options (như hàm get_test)
        elif not q_type:
             if q.get("options") and len(q.get("options")) > 0:
                mc_count += 1
             else:
                essay_count += 1

    return mc_count, essay_count


@app.route("/api/test-deploy", methods=["GET"])
def test_deploy():
    return jsonify({"status": "SUCCESS", "version": "v1.1-MC_ESSAY_FIX"})

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
fs = GridFS(db)
print(f"✅ Connected to MongoDB database: {DB_NAME}")

def remove_id(doc):
    if not doc:
        return doc
    doc.pop("_id", None)
    return doc

def remove_id_from_list(docs):
    return [remove_id(d) for d in docs]

# Hàm lấy thời gian hiện tại theo múi giờ Việt Nam (UTC+7)
def now_vn_iso():
    return datetime.now(timezone(timedelta(hours=7))).isoformat()

# ------------------ GENERIC ERROR HANDLER ------------------
@app.errorhandler(Exception)
def handle_exception(e):
    """
    Xử lý lỗi toàn cục — luôn trả về JSON thay vì HTML
    """
    if isinstance(e, HTTPException):
        return jsonify({
            "success": False,
            "message": e.description
        }), e.code

    return jsonify({
        "success": False,
        "message": "Internal server error",
        "error": str(e)
    }), 500


# ------------------ HEALTH CHECK ------------------
@app.route("/healthz", methods=["GET"])
def health():
    """
    Kiểm tra tình trạng server và kết nối MongoDB.
    """
    try:
        db_stats = db.command("ping")
        db_status = "connected" if db_stats.get("ok") == 1.0 else "error"
    except Exception as e:
        db_status = f"error: {str(e)}"

    return jsonify({
        "status": "ok",
        "timezone": "UTC+7",
        "db": DB_NAME,
        "db_status": db_status
    })


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
@app.route("/api/users", methods=["POST"]) # ✅ Bổ sung POST /api/users
def register():
    data = request.get_json() or {}
    user = data.get("user"); passwd = data.get("pass")
    
    # ✅ THÊM CÁC TRƯỜNG MỚI
    fullName = data.get("fullName"); className = data.get("className")
    gender = data.get("gender") # đã có từ trước
    # ✅ LOGIC ĐỒNG BỘ: Chuyển vai trò 'admin' thành 'teacher' khi lưu
    role_to_save = data.get("role", "student")
    if role_to_save == "admin":
        role_to_save = "teacher" # Buộc lưu là 'teacher' để đồng bộ với Frontend
    if not user or passwd is None:
        return jsonify({"success": False, "message": "Missing user or pass"}), 400
    if db.users.find_one({"user": user}):
        return jsonify({"success": False, "message": "Tên tài khoản đã tồn tại."}), 409
    
    new_user = {
        "id": str(uuid4()), 
        "user": user, 
        "pass": passwd, 
        "fullName": fullName,  # ✅ LƯU HỌ TÊN
        "className": className, # ✅ LƯU LỚP
        "gender": gender, 
        "role": data.get("role", "student") # Lấy role từ payload, mặc định là student
    }
    db.users.insert_one(new_user)
    to_return = new_user.copy()
    to_return.pop("_id", None)
    return jsonify({"success": True, "user": to_return}), 201

# --------------------- USERS ---------------------
@app.route("/users", methods=["GET"])
@app.route("/api/users", methods=["GET"])
def get_users():
    query = {}
    
    # 1. Lọc theo Vai trò
    role = request.args.get("role")
    if role:
        query["role"] = role
        
    # 2. Lọc theo Lớp
    className = request.args.get("class")
    if className:
        # Tìm kiếm chính xác tên lớp (nếu muốn tìm kiếm tương đối thì dùng $regex)
        query["className"] = className 
        
    # 3. Tìm kiếm theo Tên (Tìm kiếm tương đối)
    nameSearch = request.args.get("name")
    if nameSearch:
        # Tìm kiếm không phân biệt chữ hoa/thường trong trường 'fullName'
        query["fullName"] = {"$regex": nameSearch, "$options": "i"} 
        
    # Lọc theo Giới tính ✅ BỔ SUNG LỌC GIỚI TÍNH
    gender = request.args.get("gender")
    if gender:
        query["gender"] = gender 
    
    # Thực hiện truy vấn và loại trừ _id
    docs = list(db.users.find(query, {"_id": 0}))
    
    # Nếu bạn dùng phân trang, logic sẽ phức tạp hơn:
    # total_users = db.users.count_documents(query)
    # limit = int(request.args.get("limit", 10))
    # offset = int(request.args.get("page", 1) - 1) * limit
    # docs = list(db.users.find(query, {"_id": 0}).skip(offset).limit(limit))
    # return jsonify({"total": total_users, "users": docs})

    return jsonify(docs)

@app.route("/users/<user_id>", methods=["GET"])
@app.route("/api/users/<user_id>", methods=["GET"])
def get_user(user_id):
    """Bổ sung: Lấy thông tin người dùng theo ID để hỗ trợ Sửa (Edit)"""
    doc = db.users.find_one({"id": user_id}, {"_id": 0})
    if not doc:
        return jsonify({"message": "Người dùng không tìm thấy."}), 404
    return jsonify(doc)


@app.route("/users/<user_id>", methods=["PUT", "PATCH"])
@app.route("/api/users/<user_id>", methods=["PUT", "PATCH"])
def update_user(user_id):
    """Bổ sung: Xử lý yêu cầu Sửa/Cập nhật (PUT) thông tin người dùng."""
    data = request.get_json() or {}
    update_fields = {}
    
    # Sử dụng các trường 'user' và 'pass' nhất quán với route /login và /register
    if "user" in data:
        update_fields["user"] = data["user"]
    if "pass" in data:
        update_fields["pass"] = data["pass"]
    if "role" in data:
        role_to_update = data["role"]
        if role_to_update == "admin":
            role_to_update = "teacher" # Buộc lưu là 'teacher' để đồng bộ với Frontend
        update_fields["role"] = role_to_update
    if "fullName" in data: 
        update_fields["fullName"] = data["fullName"] # ✅ TRƯỜNG MỚI
    if "className" in data: 
        update_fields["className"] = data["className"] # ✅ TRƯỜNG MỚI    
    if "dob" in data:
        update_fields["dob"] = data["dob"]
    if "gender" in data:
        update_fields["gender"] = data["gender"]
        
    if not update_fields:
        return jsonify({"message": "Không có trường nào được cung cấp để cập nhật."}), 400

    # Cập nhật trong MongoDB dựa trên trường 'id'
    res = db.users.update_one({"id": user_id}, {"$set": update_fields})

    if res.matched_count == 0:
        return jsonify({"message": "Người dùng không tìm thấy."}), 404
    
    updated_user = db.users.find_one({"id": user_id}, {"_id": 0})
    return jsonify(updated_user), 200 # Trả về 200 OK với dữ liệu cập nhật

@app.route("/users/<user_id>", methods=["DELETE"])
@app.route("/api/users/<user_id>", methods=["DELETE"])
def delete_user(user_id):
    res = db.users.delete_one({"id": user_id})
    if res.deleted_count > 0:
        return "", 204
    return jsonify({"message": "Người dùng không tìm thấy."}), 404

# --------------------- QUESTIONS ---------------------
@app.route("/questions/image/<file_id>", methods=["GET"])
def get_question_image(file_id):
    """Trả ảnh từ GridFS"""
    try:
        file_obj = fs.get(ObjectId(file_id))
        return send_file(file_obj, mimetype=file_obj.content_type, as_attachment=False, download_name=file_obj.filename)
    except Exception as e:
        return jsonify({"message": f"File not found: {str(e)}"}), 404

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
    data = request.form
    image_file = request.files.get("image")

    image_id = None

    # 1. Upload ảnh lên GridFS nếu có
    if image_file:
        filename = secure_filename(image_file.filename)
        content_type = image_file.mimetype
        try:
            image_id = fs.put(image_file, filename=filename, content_type=content_type)
        except Exception as e:
            return jsonify({"message": f"Lỗi lưu file: {str(e)}"}), 500

    # 2. Parse options/answer
    try:
        options = json.loads(data.get("options", "[]"))
        answer = data.get("answer", "")
    except json.JSONDecodeError:
        return jsonify({"message": "Lỗi định dạng dữ liệu Options hoặc Answer."}), 400

    newq = {
        "id": str(uuid4()),
        "q": data.get("q"),
        "type": data.get("type"),
        "points": int(data.get("points", 1)),
        "subject": data.get("subject"),
        "level": data.get("level"),
        "difficulty": data.get("difficulty", "medium"),
        "options": options,
        "answer": answer,
        "imageId": str(image_id) if image_id else None
    }

    db.questions.insert_one(newq)
    to_return = newq.copy()
    to_return.pop("_id", None)
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
    data = request.form
    image_file = request.files.get("image")
    remove_old = data.get("removeOldImage", "false") == "true"

    # 1. Lấy câu hỏi hiện tại
    question = db.questions.find_one({"id": q_id})
    if not question:
        return jsonify({"message": "Không tìm thấy câu hỏi"}), 404

    image_id = question.get("imageId")

    # 2. Xóa ảnh cũ nếu user bấm remove
    if remove_old and image_id:
        try:
            fs.delete(ObjectId(image_id))
        except Exception:
            pass
        image_id = None

    # 3. Upload ảnh mới nếu có
    if image_file:
        try:
            filename = secure_filename(image_file.filename)
            content_type = image_file.mimetype
            new_image_id = fs.put(image_file, filename=filename, content_type=content_type)
            image_id = str(new_image_id)
        except Exception as e:
            return jsonify({"message": f"Lỗi upload ảnh mới: {str(e)}"}), 500

    # 4. Parse options/answer
    try:
        options = json.loads(data.get("options", "[]"))
        answer = data.get("answer", "")
    except json.JSONDecodeError:
        return jsonify({"message": "Lỗi định dạng dữ liệu Options hoặc Answer."}), 400

    # 5. Chuẩn bị dữ liệu update
    update_fields = {
        "q": data.get("q"),
        "type": data.get("type"),
        "points": int(data.get("points", 1)),
        "subject": data.get("subject"),
        "level": data.get("level"),
        "difficulty": data.get("difficulty", "medium"),
        "options": options,
        "answer": answer,
        "imageId": image_id
    }

    # 6. Cập nhật MongoDB
    res = db.questions.update_one({"id": q_id}, {"$set": update_fields})
    if res.matched_count == 0:
        return jsonify({"message": "Câu hỏi không tồn tại."}), 404

    updated = db.questions.find_one({"id": q_id}, {"_id": 0})
    return jsonify(updated), 200


@app.route("/questions/<q_id>", methods=["DELETE"])
@app.route("/api/questions/<q_id>", methods=["DELETE"])
def delete_question(q_id):
    res = db.questions.delete_one({"id": q_id})
    if res.deleted_count > 0:
        return "", 204
    return jsonify({"message": "Câu hỏi không tìm thấy."}), 404

@app.route("/images/<image_id>", methods=["GET"])
def get_image(image_id):
    try:
        file_obj = fs.get(ObjectId(image_id))
        return app.response_class(file_obj.read(), mimetype=file_obj.content_type)
    except Exception as e:
        return jsonify({"message": "Không tìm thấy ảnh", "error": str(e)}), 404



# --------------------- TESTS & QUIZ ---------------------
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
    
@app.route("/quizzes/<test_id>", methods=["GET"])
@app.route("/api/quizzes/<test_id>", methods=["GET"])
@app.route("/tests/<test_id>", methods=["GET"])
@app.route("/api/tests/<test_id>", methods=["GET"])
def get_test(test_id):
    """
    Lấy đề thi theo test_id. Hỗ trợ:
      - tests collection lưu mảng question ids (strings hoặc ObjectId)
      - tests collection lưu mảng objects (rút gọn) cần bù đắp nội dung
    Trả về doc với field 'questions' là list các question full (mỗi question có 'id' là string).
    """
    # Tìm trong tests trước, fallback sang quizzes
    doc = db.tests.find_one({"id": test_id}, {"_id": 0})
    if not doc:
        doc = db.quizzes.find_one({"id": test_id}, {"_id": 0})

    if not doc:
        return jsonify({"message": "Bài kiểm tra không tồn tại."}), 404

    question_list = doc.get("questions", [])
    # Nếu không có questions hoặc rỗng: trả nguyên doc
    if not question_list:
        return jsonify(doc)

    # Chuẩn hoá: nếu items là dicts và đã có nội dung (q hoặc question), trả luôn
    first_item = question_list[0]
    if isinstance(first_item, dict):
        # Nếu mọi object đã có field 'q' (nội dung) hoặc 'question', coi là đầy đủ
        if all(isinstance(x, dict) and ("q" in x or "question" in x) for x in question_list):
            return jsonify(doc)
        # Nếu là list of dict nhưng rút gọn (chỉ id/_id), ta phải bù đắp
        # trích các id string cần resolve
        ids_to_resolve = []
        for q in question_list:
            qid = None
            if isinstance(q.get("id"), str) and q.get("id").strip():
                qid = q.get("id")
            elif q.get("_id"):
                qid = str(q.get("_id"))
            if qid:
                ids_to_resolve.append(qid)
    elif isinstance(first_item, str):
        # list of ids (string) - xử lý bình thường
        ids_to_resolve = question_list.copy()
    else:
        # Không xác định shape -> trả nguyên doc
        return jsonify(doc)

    if not ids_to_resolve:
        return jsonify(doc)

    # Phân loại ids: ObjectId-able vs UUID strings
    object_ids = []
    uuid_strings = []
    for qid_str in ids_to_resolve:
        try:
            object_ids.append(ObjectId(qid_str))
        except Exception:
            uuid_strings.append(qid_str)

    # Tạo query $or
    or_clauses = []
    if object_ids:
        or_clauses.append({"_id": {"$in": object_ids}})
    if uuid_strings:
        or_clauses.append({"id": {"$in": uuid_strings}})

    full_questions = []
    if or_clauses:
        full_questions = list(db.questions.find(
    {"$or": or_clauses},
    {
        "_id": 1,
        "id": 1,
        "q": 1,
        "options": 1,
        "points": 1,
        "type": 1,
        "subject": 1,
        "level": 1,
        "difficulty": 1,
        "answer": 1,
        "imageId": 1, 
    }
))

    # Map bằng cả id (uuid) và str(_id)
    id_map = {}
    for q in full_questions:
        # convert _id to string key
        if q.get("_id") is not None:
            id_map[str(q["_id"])] = q
        if q.get("id"):
            id_map[q["id"]] = q

    # Xây final_questions giữ nguyên thứ tự ban đầu
    final_questions = []
    if isinstance(first_item, dict):
        # mapping nhanh bằng id/_id lấy từ object rút gọn
        for q_lite in question_list:
            # thử lấy id hoặc _id string
            id_key = None
            if isinstance(q_lite.get("id"), str) and q_lite.get("id").strip():
                id_key = q_lite.get("id")
            elif q_lite.get("_id"):
                id_key = str(q_lite.get("_id"))
            if id_key and id_key in id_map:
                q_full = id_map[id_key].copy()
                # chuẩn hoá: convert _id thành string và đảm bảo 'id' field tồn tại
                q_full["_id"] = str(q_full.get("_id")) if q_full.get("_id") is not None else None
                q_full["id"] = q_full.get("id") or q_full["_id"]
                # Loại bỏ trường nội bộ Mongo nếu bạn không muốn trả về _id thô
                # nếu muốn xóa: q_full.pop("_id", None)
                final_questions.append(q_full)
            else:
                # không tìm thấy bản đầy đủ -> giữ nguyên object rút gọn
                final_questions.append(q_lite)
    else:
        # list of ids (strings)
        for qid in ids_to_resolve:
            if qid in id_map:
                q_full = id_map[qid].copy()
                q_full["_id"] = str(q_full.get("_id")) if q_full.get("_id") is not None else None
                q_full["id"] = q_full.get("id") or q_full["_id"]
                final_questions.append(q_full)
            else:
                # không tìm thấy -> skip hoặc giữ id rỗng; mình sẽ skip
                app.logger.warning(f"Question id {qid} not found in questions collection.")
                # bạn có thể append placeholder nếu muốn
                # final_questions.append({"id": qid, "q": "(Không tìm thấy nội dung)"})

    # Gán lại questions và trả
    doc["questions"] = final_questions
    # 🔹 BỔ SUNG: Đảm bảo mọi câu hỏi đều có field 'type'
    for q in doc.get("questions", []):
        # Nếu chưa có type, tự xác định
        if "type" not in q or not q["type"]:
            if q.get("options") and len(q["options"]) > 0:
                q["type"] = "mc"  # trắc nghiệm
            else:
                q["type"] = "essay"  # tự luận
    return jsonify(doc)

@app.route("/tests", methods=["POST"])
@app.route("/api/tests", methods=["POST"])
def create_test():
    data = request.get_json() or {}

    # Normalize/transform incoming questions to list of IDs
    incoming_questions = data.get("questions", [])
    question_ids = []

    try:
        for q in incoming_questions:
            # If string -> assume it's an ID
            if isinstance(q, str):
                question_ids.append(q)
            # If dict with id or _id -> use that id
            elif isinstance(q, dict):
                if q.get("id"):
                    question_ids.append(q.get("id"))
                elif q.get("_id"):
                    # Chuyển ObjectId về string nếu cần
                    question_ids.append(str(q.get("_id"))) 
                # If dict looks like a full question (has 'q' text), insert into questions collection
                elif q.get("q") or q.get("question"):
                    new_q = {
                        "id": str(uuid4()),
                        "q": q.get("q") or q.get("question"),
                        "imageUrl": q.get("imageUrl"),
                        "type": q.get("type"),
                        "points": int(q.get("points", 1)),
                        "subject": q.get("subject"),
                        "level": q.get("level"),
                        "difficulty": q.get("difficulty", "medium"),
                        "options": q.get("options", []),
                        "answer": q.get("answer", "")
                    }
                    db.questions.insert_one(new_q)
                    question_ids.append(new_q["id"])
                # else skip unknown object
            
        # 🔥 BƯỚC 1: TÍNH TOÁN SỐ CÂU TN/TL (THÊM VÀO ĐÂY)
        mc_count, essay_count = calculate_question_counts(question_ids, db)
        
        # build test doc
        newt = {
            "id": str(uuid4()),
            "name": data.get("name"),
            "time": data.get("time"),
            "subject": data.get("subject"),
            "level": data.get("level"),
            "questions": question_ids,
            "mcCount": mc_count,     # <-- LƯU KẾT QUẢ TÍNH TOÁN
            "essayCount": essay_count, # <-- LƯU KẾT QUẢ TÍNH TOÁN
            "count": len(question_ids),
            "teacherId": data.get("teacherId"),
            "createdAt": now_vn_iso(),
            "isAutoGenerated": False # Đánh dấu thủ công rõ ràng hơn
        }
        db.tests.insert_one(newt)
        to_return = newt.copy(); to_return.pop("_id", None)
        return jsonify(to_return), 201

    except Exception as e:
        print("Error in create_test:", e)
        return jsonify({"message": "Không thể tạo đề thi.", "error": str(e)}), 500


#from uuid import uuid4
#import datetime
from flask import request, jsonify

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
        # KHÔNG LOẠI BỎ _id: Cần có _id để truy vấn sau này
        all_q = list(db.questions.find(q))
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
        candidates = list(db.questions.find({}))
        import random
        random.shuffle(candidates)
        # avoid duplicates by _id
        existing_ids = {str(q.get("_id")) for q in selected}
        added = []
        for c in candidates:
            if str(c.get("_id")) in existing_ids:
                continue
            added.append(c)
            existing_ids.add(str(c.get("_id")))
            if len(added) >= remain:
                break
        selected += added

    selected = selected[:total]

    # 👇 CHỈ LƯU TRỮ DANH SÁCH ID CÂU HỎI (STRING)
    questions_for_db = []
    for q in selected:
        q_id_str = q.get("id") or str(q.get("_id"))
        if q_id_str:
            questions_for_db.append(q_id_str)
            
    # 🔥 BƯỚC MỚI: TÍNH VÀ LƯU SỐ CÂU TN/TL CHO ĐỀ TẠO TỰ ĐỘNG
    mc_count, essay_count = calculate_question_counts(questions_for_db, db)

    newt = {
        "id": str(uuid4()),
        "name": name,
        "time": time,
        "subject": subject,
        "level": level,
        "questions": questions_for_db, 
        "mcCount": mc_count,     # <-- THÊM
        "essayCount": essay_count, # <-- THÊM
        "count": len(questions_for_db),
        "teacherId": data.get("teacherId"),
        "createdAt": now_vn_iso(),
        "isAutoGenerated": True
    }
    db.tests.insert_one(newt)
    to_return = newt.copy(); to_return.pop("_id", None)
    return jsonify(to_return), 201

@app.route("/tests/<test_id>", methods=["PUT"])
@app.route("/api/tests/<test_id>", methods=["PUT"])
def update_test(test_id):
    data = request.get_json() or {}
    # Normalize incoming questions similarly to create_test
    incoming_questions = data.get("questions", None)

    try:
        update_doc = data.copy()
        update_doc.pop("_id", None)

        if incoming_questions is not None:
            question_ids = []
            for q in incoming_questions:
                if isinstance(q, str):
                    question_ids.append(q)
                elif isinstance(q, dict):
                    if q.get("id"):
                        question_ids.append(q.get("id"))
                    elif q.get("_id"):
                        question_ids.append(q.get("_id"))
                    elif q.get("q") or q.get("question"):
                        # insert new question doc
                        new_q = {
                            "id": str(uuid4()),
                            "q": q.get("q") or q.get("question"),
                            "imageUrl": q.get("imageUrl"),
                            "type": q.get("type"),
                            "points": int(q.get("points", 1)),
                            "subject": q.get("subject"),
                            "level": q.get("level"),
                            "difficulty": q.get("difficulty", "medium"),
                            "options": q.get("options", []),
                            "answer": q.get("answer", "")
                        }
                        db.questions.insert_one(new_q)
                        question_ids.append(new_q["id"])
            update_doc["questions"] = question_ids

        # 🔥 BƯỚC MỚI: TÍNH VÀ LƯU SỐ CÂU TN/TL
        if "questions" in update_doc:
            # Truyền mảng ID câu hỏi và đối tượng DB
            mc_count, essay_count = calculate_question_counts(update_doc["questions"], db)
            update_doc["mcCount"] = mc_count
            update_doc["essayCount"] = essay_count
        
        res = db.tests.update_one({"id": test_id}, {"$set": update_doc})
        if res.matched_count > 0:
            updated = db.tests.find_one({"id": test_id}, {"_id": 0})
            return jsonify(updated)
        return jsonify({"message": "Bài kiểm tra không tồn tại."}), 404

    except Exception as e:
        print("Error in update_test:", e)
        return jsonify({"message": "Không thể cập nhật đề thi.", "error": str(e)}), 500


@app.route("/tests/<test_id>", methods=["DELETE"])
@app.route("/api/tests/<test_id>", methods=["DELETE"])
def delete_test(test_id):
    try:
        # Tìm và xóa đề theo id
        result = db.tests.delete_one({"id": test_id})
        if result.deleted_count == 0:
            return jsonify({"message": "Bài kiểm tra không tồn tại."}), 404
        return jsonify({"message": "Đã xóa đề thi thành công!"}), 200
    except Exception as e:
        print("Error deleting test:", e)
        return jsonify({"message": "Không thể xóa đề thi.", "error": str(e)}), 500

# --------------------- ASSIGNS ---------------------
@app.route("/assigns", methods=["GET"])
@app.route("/api/assigns", methods=["GET"])
def list_assigns():
    """
    API tổng hợp cho giáo viên (Admin View).
    Dùng Aggregation để JOIN Assignments + Tests + Results.
    """
    try:
        studentId = request.args.get("studentId")
        # Không cần studentId nếu dùng cho giáo viên (có thể thêm logic sau)
        # Nếu không có studentId, trả về tất cả assignments nếu cần
        if not studentId:
             # Nếu không có studentId (dùng cho Teacher), sẽ lấy tất cả assignments
             match_stage = {}
        else:
             match_stage = {"studentId": studentId}


        pipeline = [
            {"$match": match_stage},

            # Join tests (sử dụng id trong testInfo)
            {
                "$lookup": {
                    "from": "tests",
                    "localField": "testId",
                    "foreignField": "id",
                    "as": "testInfo"
                }
            },
            {"$unwind": {"path": "$testInfo", "preserveNullAndEmptyArrays": True}},

            # Join results (sử dụng assignmentId)
            {
                "$lookup": {
                    "from": "results",
                    "localField": "id",
                    "foreignField": "assignmentId",
                    "as": "resultInfo"
                }
            },
            {"$unwind": {"path": "$resultInfo", "preserveNullAndEmptyArrays": True}},
            
            {
                "$project": {
                    "_id": 0,
                    "id": 1,
                    "testId": 1,
                    "studentId": 1,
                    "deadline": 1,
                    "status": 1,
                    "assignedAt": {"$ifNull": ["$assignedAt", "$timeAssigned"]}, # Chuẩn hóa trường assignedAt
                    "submittedAt": "$resultInfo.submittedAt",
                    
                    # ✅ LẤY TỪ RESULTS ĐÃ CHUẨN HÓA
                    "gradingStatus": "$resultInfo.gradingStatus",
                    "totalScore": {"$ifNull": ["$resultInfo.totalScore", None]},
                    "mcScore": {"$ifNull": ["$resultInfo.mcScore", None]},
                    "essayScore": {"$ifNull": ["$resultInfo.essayScore", None]},
                    
                    # Thông tin Test
                    "testName": "$testInfo.name",
                    "subject": "$testInfo.subject",
                    "time": "$testInfo.time",
                    "mcCount": "$testInfo.mcCount",
                    "essayCount": "$testInfo.essayCount",
                }
            }
        ]

        docs = list(db.assignments.aggregate(pipeline))

        # Auto-map status submitted (dựa trên sự tồn tại của resultInfo)
        for a in docs:
            # Nếu có submittedAt, chắc chắn bài đã nộp
            if a.get("submittedAt"):
                a["status"] = "submitted"
            
            # Làm tròn điểm
            if a.get("totalScore") is not None:
                a["totalScore"] = round(a["totalScore"], 2)
            if a.get("mcScore") is not None:
                a["mcScore"] = round(a["mcScore"], 2)
            if a.get("essayScore") is not None:
                a["essayScore"] = round(a["essayScore"], 2)


        return jsonify(docs)

    except Exception as e:
        print("list_assigns error:", e)
        return jsonify([]), 500



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
        "timeAssigned": data.get("timeAssigned") or now_vn_iso()
    }
    db.assignments.insert_one(newa)
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
            "timeAssigned": now_vn_iso()
        }
        db.assignments.insert_one(newa)
        newa.pop("_id", None)
        created.append(newa)

    return jsonify({"success": True, "count": len(created), "assigns": created}), 201


@app.route("/debug/tests", methods=["GET"])
def debug_list_tests():
    docs = list(db.tests.find({}, {"_id": 0, "id": 1, "name": 1}))
    return jsonify(docs)

# --------------------- ASSIGNS (BULK ASSIGN) ---------------------
@app.route("/api/assigns/bulk", methods=["POST"])
def bulk_assign_tests():
    """
    Xử lý giao một hoặc nhiều đề thi (testIds) cho học sinh (studentIds).
    Payload dự kiến: {testIds: array, studentIds: array, teacherId: string, deadline: string | null}
    """
    try:
        data = request.get_json() or {}
        
        # 1. Lấy dữ liệu từ Frontend
        test_ids = data.get("testIds", [])      
        student_ids = data.get("studentIds", [])
        teacher_id = data.get("teacherId")
        deadline_iso = data.get("deadline") 
        
        # Kiểm tra dữ liệu đầu vào cơ bản
        if not isinstance(test_ids, list) or not isinstance(student_ids, list) or not teacher_id:
            return jsonify({"message": "Dữ liệu đầu vào thiếu hoặc không đúng định dạng (testIds, studentIds, teacherId).", "count": 0}), 400
        
        # Đảm bảo phải có đề thi và học sinh để giao
        if not test_ids or not student_ids:
            return jsonify({"message": "Vui lòng chọn ít nhất một đề thi và một học sinh.", "count": 0}), 400
        
        # 2. Lấy thông tin học sinh dựa trên student_ids
        students_cursor = db.users.find(
            {"id": {"$in": student_ids}}, 
            {"id": 1, "fullName": 1, "className": 1}
        )
        student_map = {s['id']: s for s in students_cursor}
        valid_student_ids = list(student_map.keys())
        
        if not valid_student_ids:
            return jsonify({"message": f"Không tìm thấy học sinh hợp lệ nào từ danh sách đã chọn.", "count": 0}), 200

        # 3. Lấy thông tin chi tiết của tất cả đề thi
        test_docs_cursor = db.tests.find(
            {"id": {"$in": test_ids}},
            {"_id": 0, "id": 1, "name": 1, "subject": 1}
        )
        test_map = {t['id']: t for t in test_docs_cursor}

        assignments_to_insert = []
        
        # 4. Xử lý Logic Giao Bài (Vòng lặp lồng nhau)
        for t_id in test_ids:
            test_info = test_map.get(t_id)
            if not test_info:
                print(f"⚠️ Test ID {t_id} không tìm thấy. Bỏ qua.")
                continue

            for stu_id in valid_student_ids:
                student = student_map.get(stu_id) 
                if not student: continue 
                
                # Kiểm tra bài giao đã tồn tại
                existing_assignment = db.assignments.find_one({
                    "testId": t_id,
                    "studentId": stu_id,
                })

                if existing_assignment:
                    # Nếu đã giao: CẬP NHẬT deadline, teacherId và assignedAt (trong trường hợp assignedAt bị thiếu)
                    update_set = {
                        "teacherId": teacher_id,
                        "deadline": deadline_iso,
                    }
                    # Đảm bảo assignedAt tồn tại cho các bản ghi cũ
                    if "assignedAt" not in existing_assignment and "createdAt" not in existing_assignment:
                         update_set["assignedAt"] = now_vn_iso()
                         
                    db.assignments.update_one(
                        {"id": existing_assignment["id"]},
                        {"$set": update_set}
                    )
                else:
                    # Nếu chưa giao: CHÈN MỚI
                    new_assign = {
                        "id": str(uuid4()), 
                        "testId": t_id,
                        "testName": test_info.get("name"), 
                        "studentId": stu_id,
                        "studentName": student.get("fullName"), 
                        "className": student.get("className"), 
                        "teacherId": teacher_id,
                        "deadline": deadline_iso,
                        "status": "pending",
                        "assignedAt": now_vn_iso(), # ✅ GHI BẰNG assignedAt
                    }
                    assignments_to_insert.append(new_assign)
        
        # 5. Chèn tất cả bài giao mới (Bulk Insert)
        if assignments_to_insert:
            db.assignments.insert_many(assignments_to_insert)

        # 6. Cập nhật trạng thái "Đã giao" cho các đề thi đã chọn
        db.tests.update_many(
            {"id": {"$in": test_ids}},
            {"$set": {"assignmentStatus": "assigned"}}
        )

        total_processed_count = len(test_ids) * len(valid_student_ids) 
        
        # 7. Trả về kết quả
        return jsonify({
            "success": True, 
            "count": len(test_ids),
            "totalAssignmentsProcessed": total_processed_count,
            "message": f"Đã giao thành công {len(test_ids)} đề thi cho {len(valid_student_ids)} học sinh (Tổng: {total_processed_count} bài giao)."
        }), 201

    except Exception as e:
        print(f"Lỗi khi thực hiện bulk_assign_tests: {e}")
        return jsonify({"message": "Lỗi máy chủ khi giao/cập nhật đề.", "count": 0}), 500

@app.route("/api/tests/<test_id>/assignments", methods=["GET"])
def get_test_assignments(test_id):
    """
    Lấy danh sách assignments chi tiết cho một đề thi, 
    bao gồm tên học sinh và trạng thái làm bài (done).
    """
    try:
        # 1. Lấy tất cả Assignments cho test_id
        assignments = list(db.assignments.find({"testId": test_id}, {"_id": 0}))
        
        # 2. Lấy danh sách ID học sinh
        student_ids = [a.get("studentId") for a in assignments if a.get("studentId")]
        
        # 3. Lấy thông tin học sinh (Tên, Lớp, Role)
        # 🔥 ĐÃ SỬA: Truy vấn 'fullName' và 'className' thay vì 'name' và 'class'
        students_cursor = db.users.find(
            {"id": {"$in": student_ids}}, 
            {"_id": 0, "id": 1, "fullName": 1, "className": 1, "role": 1}
        )
        student_map = {s["id"]: s for s in students_cursor}

        # 4. Ghép dữ liệu và trả về
        results = []
        for a in assignments:
            # 🔥 ĐÃ SỬA: Đảm bảo sử dụng 'fullName' và 'className'
            student_info = student_map.get(a.get("studentId"), {
                "fullName": "Không rõ", 
                "className": "N/A",
                "role": "student"
            })
            
            # Gán dữ liệu cho Frontend (sử dụng .get() an toàn hơn)
            a['studentName'] = student_info.get('fullName', 'Không rõ') 
            a['studentClass'] = student_info.get('className', 'N/A')
            a['studentRole'] = student_info.get('role', 'student')
            
            results.append(a)
            
        return jsonify(results), 200

    except Exception as e:
        # Bạn nên sử dụng logging thay vì print trong môi trường production
        print(f"Lỗi khi lấy assignment cho test {test_id}: {e}")
        return jsonify({"message": "Lỗi máy chủ."}), 500

@app.route("/api/assignments/bulk-delete", methods=["POST"])
def bulk_delete_assignments():
    """Xóa nhiều assignments cùng lúc dựa trên danh sách ID."""
    data = request.get_json() or {}
    assignment_ids = data.get("assignmentIds", [])

    if not assignment_ids:
        return jsonify({"message": "Thiếu danh sách assignmentIds", "deletedCount": 0}), 400

    try:
        # Xóa tất cả tài liệu có ID nằm trong danh sách
        result = db.assignments.delete_many({"id": {"$in": assignment_ids}})
        
        return jsonify({"message": f"Đã xóa {result.deleted_count} assignments.", "deletedCount": result.deleted_count}), 200

    except Exception as e:
        print(f"Lỗi khi xóa hàng loạt assignments: {e}")
        return jsonify({"message": "Lỗi máy chủ khi xóa hàng loạt assignment.", "deletedCount": 0}), 500

# --------------------- ASSIGNMENTS (Student View) ---------------------
@app.route("/api/assignments", methods=["GET"])
def get_assignments_for_student():
    student_id = request.args.get("studentId")
    if not student_id:
        return jsonify({"success": False, "message": "Missing studentId parameter"}), 400

    # Tìm tất cả assignments cho student_id này chưa nộp (status != done)
    assignments = list(db.assignments.find({
        "studentId": student_id,
        "status": {"$in": ["pending", "assigned", None]} # Chỉ lấy các bài chưa làm/đang chờ
    }, {"_id": 0})) 

    if not assignments:
        return jsonify({"success": True, "assignments": []})

    # Gộp thông tin bài thi (testName, subject, time,...)
    test_ids = [a["testId"] for a in assignments if a.get("testId")]
    tests = db.tests.find({"id": {"$in": test_ids}}, 
                           {"_id": 0, "id": 1, "name": 1, "subject": 1, "time": 1, "mcCount": 1, "essayCount": 1})
    tests_map = {t["id"]: t for t in tests}

    # Tạo danh sách kết quả cuối cùng
    result_list = []
    for a in assignments:
        test_info = tests_map.get(a["testId"], {})
        
        # 🔥 FIX: Ưu tiên lấy assignedAt, nếu không có thì lấy createdAt để tương thích với bản ghi cũ
        assigned_date = a.get("assignedAt") or a.get("createdAt") 
        
        result_list.append({
            "assignmentId": a.get("id"),
            "testId": a["testId"],
            "testName": test_info.get("name", a.get("testName", "N/A")), # Fallback về testName trong assignment
            "subject": test_info.get("subject", "N/A"),
            "time": test_info.get("time"),
            "mcCount": test_info.get("mcCount", 0),
            "essayCount": test_info.get("essayCount", 0),
            "deadline": a.get("deadline"),
            "assignedAt": assigned_date, # ✅ TRUYỀN DỮ LIỆU ĐÃ ĐƯỢC CHUẨN HÓA
            "status": a.get("status", "pending"),
        })
        
    return jsonify({"success": True, "assignments": result_list})


# --------------------- RESULTS ---------------------
@app.route("/results", methods=["POST"])
@app.route("/api/results", methods=["POST"])
def create_result():
    """
    Tạo/Cập nhật Result khi học sinh nộp bài.
    - Mở rộng: cố gắng match questions theo 'id' hoặc '_id' (ObjectId).
    - Robust: normalize question IDs, robust student answer map,
      chuẩn hoá so sánh đáp án, in debug khi mismatch.
    """
    try:
        data = request.get_json() or {}
        student_id = data.get("studentId") or data.get("student_id")
        assignment_id = data.get("assignmentId") or data.get("assignment_id")
        test_id = data.get("testId") or data.get("test_id")
        student_answers = data.get("studentAnswers", []) or data.get("answers", []) or []

        if not student_id or not assignment_id or not test_id:
            return jsonify({"message": "Thiếu ID (studentId, assignmentId, testId)"}), 400

        # 1. Lấy thông tin Test
        test_doc = db.tests.find_one({"id": test_id})
        if not test_doc:
            return jsonify({"message": "Không tìm thấy đề thi"}), 404

        test_questions = test_doc.get("questions", []) or []

        # 2. NORMALIZE tất cả question IDs (hỗ trợ dict có id, _id, questionId, hoặc chuỗi)
        q_ids = []
        raw_obj_ids = []
        for q_entry in test_questions:
            if isinstance(q_entry, dict):
                candidate = q_entry.get("id") or q_entry.get("_id") or q_entry.get("questionId") or q_entry.get("question_id")
                if candidate is not None:
                    s = str(candidate)
                    q_ids.append(s)
                    # nếu là 24 hex, giữ để convert ObjectId thử match
                    if len(s) == 24:
                        raw_obj_ids.append(s)
            else:
                s = str(q_entry)
                q_ids.append(s)
                if len(s) == 24:
                    raw_obj_ids.append(s)

        # loại bỏ rỗng và unique
        q_ids = [str(x) for x in q_ids if x and str(x).strip()]
        q_ids = list(dict.fromkeys(q_ids))

        # chuẩn bị list ObjectId nếu có
        obj_ids = []
        for s in list(dict.fromkeys(raw_obj_ids)):
            try:
                obj_ids.append(ObjectId(s))
            except Exception:
                pass

        if not q_ids and not obj_ids:
            return jsonify({"message": "Đề thi không có câu hỏi hợp lệ"}), 400

        # 3. Lấy chi tiết câu hỏi từ DB (questions collection)
        # Query bằng cả id (string) và _id (ObjectId) nếu tồn tại
        query_clauses = []
        if q_ids:
            query_clauses.append({"id": {"$in": q_ids}})
        if obj_ids:
            query_clauses.append({"_id": {"$in": obj_ids}})

        if query_clauses:
            question_docs = list(db.questions.find({"$or": query_clauses}, {"_id": 1, "id": 1, "q": 1, "type": 1, "points": 1, "options": 1, "answer": 1}))
        else:
            question_docs = []

        # normalize question_map: key by string id if exists, else by str(_id)
        question_map = {}
        for q in question_docs:
            if q.get("id"):
                question_map[str(q.get("id"))] = q
            # also map by _id string for fallback
            if q.get("_id"):
                question_map[str(q.get("_id"))] = q

        # Debug logs to help identify mismatch
        print(f"[DEBUG create_result] q_ids ({len(q_ids)}):", q_ids)
        print(f"[DEBUG create_result] obj_ids ({len(obj_ids)}):", [str(x) for x in obj_ids])
        print(f"[DEBUG create_result] questions fetched ({len(question_docs)}), map keys:", list(question_map.keys()))

        # 4. Tạo map câu trả lời của học sinh (hỗ trợ nhiều key)
        student_ans_map = {}
        for ans in student_answers:
            if not isinstance(ans, dict):
                continue
            qkey = ans.get("questionId") or ans.get("question_id") or ans.get("qid") or ans.get("id")
            if qkey is None:
                continue
            student_ans_map[str(qkey)] = ans

        mc_score = 0.0
        essay_count = 0
        detailed_results = []

        # helper: chuẩn hoá string để so sánh
        def norm_str(x):
            if x is None:
                return ""
            if isinstance(x, (list, dict)):
                return str(x).strip().lower()
            return str(x).strip().lower()

        # 5. LẶP VÀ TÍNH ĐIỂM (Chỉ lặp trên q_ids đã chuẩn hóa; nếu q_id không map được, thử lookup bằng ObjectId string)
        for q_id in q_ids:
            # try by direct id key
            q = question_map.get(str(q_id))
            # if not found, try treating q_id as ObjectId string
            if not q:
                try:
                    q = question_map.get(str(ObjectId(q_id)))
                except Exception:
                    q = None

            if not q:
                print(f"⚠️ Cảnh báo: Không tìm thấy chi tiết câu hỏi với ID: {q_id} (bỏ qua).")
                continue

            q_type = (q.get("type") or "mc").lower()
            try:
                max_points = float(q.get("points", 1.0))
            except (ValueError, TypeError):
                max_points = 1.0

            ans = student_ans_map.get(str(q_id), {}) or student_ans_map.get(str(q.get("id")), {}) or student_ans_map.get(str(q.get("_id")), {})
            student_ans_value = ans.get("answer") or ans.get("studentAnswer") or ans.get("value") or ans.get("selected") or ""

            is_correct = None
            points_gained = 0.0

            # Lấy correct answer từ nhiều nguồn: q.answer hoặc option.correct flag hoặc option.value/text
            correct_ans = q.get("answer")
            if not correct_ans and q.get("options"):
                for o in q.get("options", []):
                    if isinstance(o, dict) and (o.get("correct") or o.get("isCorrect")):
                        correct_ans = o.get("value") or o.get("text") or o.get("id") or correct_ans
                        break

            n_student = norm_str(student_ans_value)
            n_correct = norm_str(correct_ans)

            # Xử lý Trắc nghiệm (MC)
            if q_type in ["mc", "multiple_choice", "single_choice"]:
                if isinstance(student_ans_value, list):
                    # multi-select: compare sets (normalized)
                    student_set = set([norm_str(x) for x in student_ans_value])
                    correct_set = set([norm_str(x) for x in (correct_ans if isinstance(correct_ans, list) else [correct_ans]) if x is not None])
                    is_correct = (student_set == correct_set)
                else:
                    is_correct = (n_student == n_correct)

                if is_correct:
                    points_gained = max_points
                    mc_score += max_points
                else:
                    points_gained = 0.0

            elif q_type in ["essay", "tu_luan", "tự luận"]:
                q_type = "essay"
                essay_count += 1
                points_gained = 0.0
                is_correct = None

            else:
                if n_correct and n_student:
                    is_correct = (n_student == n_correct)
                else:
                    is_correct = False
                if is_correct:
                    points_gained = max_points
                    mc_score += max_points

            detailed_results.append({
                "questionId": q_id,
                "questionText": q.get("q"),
                "studentAnswer": student_ans_value,
                "correctAnswer": correct_ans,
                "maxPoints": max_points,
                "pointsGained": round(points_gained, 2),
                "isCorrect": is_correct,
                "type": q_type,
                "teacherScore": None,
                "teacherNote": ""
            })

        # 6. Xác định trạng thái chấm
        grading_status = "Đang Chấm" if essay_count > 0 else "Hoàn tất"
        result_id = str(uuid4())
        total_score = round(mc_score + 0.0, 2)

        new_result = {
            "id": result_id,
            "studentId": student_id,
            "assignmentId": assignment_id,
            "testId": test_id,
            "studentAnswers": student_answers,
            "detailedResults": detailed_results,
            "gradingStatus": grading_status,
            "mcScore": round(mc_score, 2),
            "essayScore": 0.0,
            "totalScore": total_score,
            "submittedAt": now_vn_iso(),
        }

        db.results.replace_one(
            {"studentId": student_id, "assignmentId": assignment_id},
            new_result,
            upsert=True
        )

        db.assignments.update_one(
            {"id": assignment_id},
            {"$set": {"status": "submitted", "submittedAt": new_result["submittedAt"]}}
        )

        print(f"[DEBUG create_result] created detailedResults count: {len(detailed_results)} for result {result_id}")

        return jsonify(new_result), 201

    except Exception as e:
        print("create_result error:", e)
        import traceback
        traceback.print_exc()
        return jsonify({"message": f"Server error: {str(e)}"}), 500
    
# Chấm bài tự luận
from flask import abort
#from datetime import datetime, timedelta

# API Chấm bài Tự luận (Thay thế grade_result)
@app.route("/api/results/<result_id>/grade", methods=["POST"])
def grade_result(result_id):
    """
    Giáo viên chấm điểm bài làm học sinh (Chỉ cần gửi các câu tự luận đã chấm).
    - Chống crash essays chứa string
    - Cập nhật điểm vào detailedResults
    - Tính toán lại totalScore, mcScore, essayScore
    - Lần 2 => Hoàn tất
    """
    try:
        data = request.get_json() or {}
        essays = data.get("essays", []) or []

        # ✅ FILTER: chỉ nhận dict — tránh "'str' has no attribute get"
        clean_essays = []
        for e in essays:
            if isinstance(e, dict):
                clean_essays.append(e)
            else:
                print("⚠️ Bỏ qua essay lỗi:", e)
        essays = clean_essays

        # === Lấy result ===
        result = db.results.find_one({"id": result_id})
        if not result:
            return jsonify({"error": "Không tìm thấy bài làm"}), 404

        # current regradeCount
        current_regrade = result.get("regradeCount", 0)

        # === map detailedResults ===
        detailed_list = result.get("detailedResults", [])
        detailed_map = { str(d["questionId"]): d for d in detailed_list if "questionId" in d }

        # === Update điểm GV ===
        for e in essays:
            qid = str(e.get("questionId"))
            if qid not in detailed_map:
                continue

            try:
                ts = float(e.get("teacherScore") or 0)
            except:
                ts = 0

            note = e.get("teacherNote") or ""

            det = detailed_map[qid]
            det["teacherScore"] = ts
            det["teacherNote"] = note
            det["pointsGained"] = ts
            det["isCorrect"] = ts > 0

        # === Tính toán lại ===
        new_total = 0.0
        mc = 0.0
        es = 0.0

        for d in detailed_map.values():
            pts = float(d.get("pointsGained") or 0)
            q_type = (d.get("type") or "").lower()
            new_total += pts

            if q_type in ["essay", "tu_luan"]:
                es += pts
            else:
                mc += pts

        graded_at = now_vn_iso()

        # === LOGIC TRẠNG THÁI CHÍNH THỨC ===
        # Lần 1: "Đã Chấm"
        # Lần 2+: "Hoàn tất"
        if current_regrade + 1 >= 2:
            new_status = "Hoàn tất"
        else:
            new_status = "Đã Chấm"

        update = {
            "detailedResults": list(detailed_map.values()),
            "totalScore": round(new_total, 2),
            "mcScore": round(mc, 2),
            "essayScore": round(es, 2),
            "gradingStatus": new_status,
            "gradedAt": graded_at,
        }

        # === SAVE ===
        db.results.update_one(
            {"id": result_id},
            {
                "$set": update,
                "$inc": { "regradeCount": 1 }
            }
        )

        return jsonify({
            "success": True,
            "message": f"{new_status}! Tổng: {round(new_total,2):.2f}",
            "totalScore": round(new_total,2),
            "gradingStatus": new_status,
            "regradeCount": current_regrade + 1
        })

    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"error": str(e), "message": "Internal Server Error"}), 500
        
# API Lấy kết quả đơn lẻ (Dành cho Debug hoặc trường hợp đơn giản)
@app.route("/results/<result_id>", methods=["GET"])
@app.route("/api/results/<result_id>", methods=["GET"])
def get_result(result_id):
    doc = db.results.find_one({"id": result_id}, {"_id": 0})
    if not doc: return jsonify({"message": "Kết quả không tìm thấy."}), 404
    return jsonify(doc)
    
def _calculate_grading_status(detailed_results):
    """
    Xác định trạng thái chấm bài dựa trên detailedResults.
    "Chưa Chấm" nếu có bất kỳ câu hỏi 'essay' nào có pointsGained == 0.
    """
    has_essay = False
    is_awaiting_manual_grade = False
    
    for detail in detailed_results:
        q_type = detail.get("type", "").lower()
        if q_type in ["essay", "tu_luan"]:
            has_essay = True
            # Nếu điểm nhận được là 0 VÀ maxPoints > 0, coi như chưa chấm
            if detail.get("pointsGained", 0) == 0 and detail.get("maxPoints", 0) > 0:
                is_awaiting_manual_grade = True
                break
    
    if is_awaiting_manual_grade:
        return "Chưa Chấm" # Cần giáo viên chấm tay
    elif has_essay:
        return "Đã Chấm" # Đã có câu tự luận nhưng đã được chấm điểm (pointsGained > 0)
    else:
        return "Hoàn tất" # Không có câu tự luận

# API mới để lấy danh sách kết quả tổng hợp cho giáo viên (Yêu cầu 1)
@app.route("/api/results_summary", methods=["GET"])
def get_results_summary():
    
    # 1. Truy vấn Aggregation để join dữ liệu (Giữ nguyên Pipeline của bạn)
    pipeline = [
        # Giai đoạn 1: Join với collection 'users'
        {
            "$lookup": {
                "from": "users",
                "localField": "studentId",
                "foreignField": "id",
                "as": "student_info"
            }
        },
        {"$unwind": {"path": "$student_info", "preserveNullAndEmptyArrays": True}},
        
        # Giai đoạn 2: Join với collection 'tests'
        {
            "$lookup": {
                "from": "tests",
                "localField": "testId",
                "foreignField": "id",
                "as": "test_info"
            }
        },
        {"$unwind": {"path": "$test_info", "preserveNullAndEmptyArrays": True}},

        # Giai đoạn 3: Project (chọn và định hình) các trường cần thiết
        {
            "$project": {
                "_id": 0, 
                "id": "$id",
                "studentId": "$studentId",
                "testId": "$testId",
                
                # ✅ ĐIỂM VÀ TRẠNG THÁI
                "totalScore": {"$ifNull": ["$totalScore", 0.0]},
                "mcScore": {"$ifNull": ["$mcScore", 0.0]},
                "essayScore": {"$ifNull": ["$essayScore", 0.0]},
                "gradingStatus": {"$ifNull": ["$gradingStatus", "Đang Chấm"]},
                "gradedAt": {"$ifNull": ["$gradedAt", None]}, 
                
                "submittedAt": "$submittedAt",
                
                # Thông tin đã Join
                "testName": {"$ifNull": ["$test_info.name", "Đã Xóa"]},
                "studentName": {"$ifNull": ["$studentName", "$student_info.fullName", "Ẩn danh"]},
                "className": {"$ifNull": ["$className", "$student_info.className", "N/A"]},
            }
        }
    ]
    
    docs = list(db.results.aggregate(pipeline))
    
    # 2. Xử lý logic nghiệp vụ (CHUẨN HÓA TRẠNG THÁI CHO FRONTEND)
    for doc in docs:
        doc.pop("detailedResults", None) 
        
        status_from_db = doc.get("gradingStatus")
        
        # 1. Trạng thái Hoàn tất (Đảm bảo tất cả các trạng thái đã xong đều là Hoàn tất)
        if status_from_db in ["Hoàn tất", "Tự động hoàn tất", "Đã Chấm Lại"]:
            doc["gradingStatus"] = "Hoàn tất"
        
        # 2. Trạng thái đã chấm (Lần 1)
        elif status_from_db == "Đã Chấm":
             doc["gradingStatus"] = "Đã Chấm" 
             
        # 3. Trạng thái Đang Chấm (Bao gồm Chưa Chấm, Lỗi, hoặc bất kỳ giá trị không hợp lệ nào)
        else:
             doc["gradingStatus"] = "Đang Chấm"
        
        # Chuyển đổi và làm tròn điểm
        doc["totalScore"] = round(doc.get("totalScore", 0.0), 2)
        doc["mcScore"] = round(doc.get("mcScore", 0.0), 2)
        doc["essayScore"] = round(doc.get("essayScore", 0.0), 2)
        
    return jsonify(docs)

from flask import jsonify
# Giả định db (MongoDB client) đã được định nghĩa và khởi tạo
# Ví dụ: from app import db


# API Lấy chi tiết Result (Đã tối ưu hóa)
@app.route("/api/results/<result_id>", methods=["GET"])
def get_result_detail(result_id):
    """
    Lấy chi tiết bài làm, kết hợp với thông tin câu hỏi và học sinh.
    Đã FIX lỗi logic isCorrect cho MC.
    """
    result = db.results.find_one({"id": result_id})
    if not result:
        return jsonify({"error": "Không tìm thấy kết quả"}), 404
        
    # 1. LẤY ĐIỂM CHUẨN HÓA TỪ DB
    db_mc_score = float(result.get("mcScore", 0.0))
    db_essay_score = float(result.get("essayScore", 0.0))
    
    # 2. Lấy thông tin user và test
    user = db.users.find_one({"id": result.get("studentId")}, {"fullName": 1, "className": 1, "_id": 0})
    test = db.tests.find_one({"id": result.get("testId")})
    
    student_name = user.get("fullName", "Ẩn danh") if user else "Ẩn danh"
    class_name = user.get("className", "N/A") if user else "N/A"
    test_name = test.get("name") if test else "Bài thi đã xóa"

    # 3. Xử lý detailedResults (Nguồn đáng tin cậy nhất)
    detailed_results = result.get("detailedResults", [])
    
    # Lấy ID câu hỏi từ detailedResults
    q_ids = [d["questionId"] for d in detailed_results if d.get("questionId")]

    # Lấy chi tiết câu hỏi từ DB (questions collection)
    question_docs = list(db.questions.find({"id": {"$in": q_ids}}))
    question_map = {q["id"]: q for q in question_docs}

    # Ghép dữ liệu
    answers = []
    for d in detailed_results:
        qid = d["questionId"]
        q = question_map.get(qid, {})
        
        # Chuẩn hóa đáp án đúng từ options nếu cần
        correct_ans_from_options = None
        # Kiểm tra type từ detailedResults trước, sau đó là từ question_map
        if (d.get("type") or q.get("type") or "").lower() == "mc" and q.get("options"):
            for opt in q["options"]:
                if opt.get("correct") is True:
                    correct_ans_from_options = opt.get("text")
                    break
        
        # Loại câu hỏi và điểm
        q_type = (d.get("type") or q.get("type") or "mc").lower()
        max_score = d.get("maxPoints", q.get("points", 0))

        # Điểm đạt được và trạng thái
        gained_score = d.get("pointsGained", 0.0)
        
        # LẤY TRẠNG THÁI ĐÚNG/SAI TỪ detailedResults
        is_correct_for_display = d.get("isCorrect") 
        
        # FIX LOGIC: Nếu là MC, và isCorrect là None, dựa vào pointsGained
        if q_type == "mc":
            if is_correct_for_display is None:
                # Nếu có điểm > 0 thì coi là đúng
                is_correct_for_display = gained_score > 0 
            
            # Đảm bảo isCorrect không bao giờ là None cho MC
            if is_correct_for_display is None:
                is_correct_for_display = False 

        # Lấy teacherScore/Note
        teacher_score = d.get("teacherScore")
        teacher_note = d.get("teacherNote")

        # Cấu trúc câu trả lời
        answers.append({
            "questionId": qid,
            "question": {
                "id": qid,
                "q": q.get("q"),
                "type": q_type,
                "points": max_score,
                "imageId": q.get("imageId"),
                "options": q.get("options", []),
                # Đáp án đúng (Ưu tiên từ result.correctAnswer, sau đó đến options)
                "correctAnswer": d.get("correctAnswer") or q.get("answer") or correct_ans_from_options,
            },
            # TRƯỜNG STUDENTANSWER/USERANSWER PHẢI CÓ ĐỂ FRONTEND SO SÁNH
            "userAnswer": d.get("studentAnswer"), 
            "maxScore": max_score,
            "gainedScore": round(gained_score, 2), 
            "correctAnswer": d.get("correctAnswer") or q.get("answer") or correct_ans_from_options,
            "isCorrect": is_correct_for_display, # TRƯỜNG NÀY PHẢI CÓ
            "isEssay": q_type in ["essay", "tu_luan"], 
            "teacherScore": teacher_score, 
            "teacherNote": teacher_note
        })

    # 4. Cấu trúc JSON cuối cùng trả về Frontend
    detail = {
        "id": result["id"],
        "studentName": result.get("studentName") or student_name,
        "className": result.get("className") or class_name,
        "testName": test_name,
        "totalScore": round(result.get("totalScore", 0), 2),
        "gradingStatus": result.get("gradingStatus", "Chưa Chấm"),
        "submittedAt": result.get("submittedAt"),
        "mcScore": round(db_mc_score, 2),
        "essayScore": round(db_essay_score, 2),
        "answers": answers
    }
    
    return jsonify(detail)


# API mới để thống kê bài giao (Yêu cầu 3)
@app.route("/api/assignment_stats", methods=["GET"])
def get_assignment_stats():
    # Giả định thống kê tổng quan:
    total_tests_assigned = db.tests.count_documents({})
    total_results_submitted = db.results.count_documents({})
    total_students = db.users.count_documents({"role": "student"})
        
    return jsonify({
        "totalTestsAssigned": total_tests_assigned,
        "totalResultsSubmitted": total_results_submitted,
        "totalStudents": total_students,
        "note": "Cần dữ liệu Assignment để tính chính xác số HS chưa nộp."
    })    


# ✅ API Lấy danh sách Results cho học sinh (Nhanh hơn Aggregation)
@app.route("/api/results", methods=["GET"])
def get_results_for_student():
    """
    Lấy tất cả các bài đã làm (Results) cho một học sinh cụ thể (sử dụng studentId).
    """
    student_id = request.args.get("studentId")
    if not student_id:
        return jsonify({"message": "Missing studentId parameter"}), 400

    try:
        # Truy vấn tất cả kết quả có studentId tương ứng
        results = list(db.results.find({"studentId": student_id}, {"_id": 0}))
        # LƯU Ý: Frontend (hàm processAssignments) mong đợi một mảng các Results.
        return jsonify(results)
    
    except Exception as e:
        print(f"Lỗi khi lấy results cho student {student_id}: {e}")
        return jsonify([]), 500

# Serve frontend files (unchanged)
@app.route("/", methods=["GET"])
def index():
    try:
        return send_from_directory(".", "index.html")
    except Exception:
        return jsonify({"message": "Index not found"}), 404

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT)
