from bson.objectid import ObjectId
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
        full_questions = list(db.questions.find({"$or": or_clauses}, {"_id": 1, "id": 1, "q": 1, "options": 1, "points": 1, "imageUrl": 1}))

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
                    question_ids.append(q.get("_id"))
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
        # build test doc
        newt = {
            "id": str(uuid4()),
            "name": data.get("name"),
            "time": data.get("time"),
            "subject": data.get("subject"),
            "level": data.get("level"),
            "questions": question_ids,
            "teacherId": data.get("teacherId"),
            "createdAt": datetime.datetime.utcnow().isoformat()
        }
        db.tests.insert_one(newt)
        to_return = newt.copy(); to_return.pop("_id", None)
        return jsonify(to_return), 201

    except Exception as e:
        print("Error in create_test:", e)
        return jsonify({"message": "Không thể tạo đề thi.", "error": str(e)}), 500


from uuid import uuid4
import datetime
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

    # ✅ BƯỚC SỬA LỖI QUAN TRỌNG: Tạo đối tượng rút gọn để lưu trữ
    questions_for_db = []
    for q in selected:
        # Chuyển đổi ObjectId sang chuỗi ID
        q_id_str = str(q.get("_id"))
        
        # Lấy các trường cần thiết cho việc hiển thị ở frontend
        q_to_save = {
            # Sử dụng '_id' thay vì 'id' nếu frontend dùng _id
            "id": q_id_str, 
            "question": q.get("question"), # Nội dung câu hỏi
            "answers": q.get("answers"), 
            "difficulty": q.get("difficulty"),
            "level": q.get("level"),
            "subject": q.get("subject")
        }
        questions_for_db.append(q_to_save)
    
    
    newt = {
        "id": str(uuid4()),
        "name": name,
        "time": time,
        "subject": subject,
        "level": level,
        "questions": questions_for_db, # <-- LƯU TRỮ ĐỐI TƯỢNG RÚT GỌN ĐẦY ĐỦ
        "count": len(questions_for_db),
        "teacherId": data.get("teacherId"),
        "createdAt": datetime.datetime.utcnow().isoformat(),
        "isAutoGenerated": True # Thêm trường này để dễ kiểm tra ở frontend
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


@app.route("/debug/tests", methods=["GET"])
def debug_list_tests():
    docs = list(db.tests.find({}, {"_id": 0, "id": 1, "name": 1}))
    return jsonify(docs)

    

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
    student_answers = data.get("studentAnswers", [])  # expecting list of {questionId, answer, type?}
    test_id = data.get("testId")

    # Lấy danh sách ID câu hỏi
    q_ids = [a.get("questionId") for a in student_answers if "questionId" in a]
    questions = list(db.questions.find(
        {"id": {"$in": q_ids}},
        {"_id": 0, "id": 1, "type": 1, "points": 1, "options": 1}
    ))

    question_map = {q["id"]: q for q in questions}
    total_score = 0
    detailed = []

    for ans in student_answers:
        qid = ans.get("questionId")
        q = question_map.get(qid)
        if not q:
            # Nếu không tìm thấy câu hỏi — lưu entry nhưng đánh dấu missing
            detailed.append({
                "questionId": qid,
                "type": ans.get("type", "mc"),
                "studentAnswer": ans.get("answer"),
                "isCorrect": False,
                "pointsGained": 0,
                "maxPoints": 0,
                "correctAnswer": None,
                "note": "question-not-found"
            })
            continue

        q_type = q.get("type")
        student_ans = ans.get("answer")
        max_points = int(q.get("points", 1))

        correct_ans = None
        # Lấy đáp án đúng từ options[]
        if q_type == "mc" and q.get("options"):
            for opt in q["options"]:
                if opt.get("correct") is True:
                    correct_ans = opt.get("text")
                    break

        # Nếu student_ans là số (index), convert sang text khi có options
        student_ans_text = student_ans
        if q_type == "mc" and q.get("options"):
            try:
                # số nguyên (index)
                if isinstance(student_ans, int):
                    idx = student_ans
                    if 0 <= idx < len(q["options"]):
                        student_ans_text = q["options"][idx].get("text")
                else:
                    # có thể là chuỗi số "2"
                    if isinstance(student_ans, str) and student_ans.isdigit():
                        idx = int(student_ans)
                        if 0 <= idx < len(q["options"]):
                            student_ans_text = q["options"][idx].get("text")
                    # nếu student_ans là object id của option hoặc giá trị nào khác, giữ nguyên
            except Exception:
                # giữ nguyên student_ans_text
                pass

        # so sánh (bỏ whitespace, so sánh string)
        is_correct = False
        if q_type == "mc":
            is_correct = (str(student_ans_text).strip() == str(correct_ans).strip()) if correct_ans is not None else False
        else:
            # cho các loại khác (essay) mặc định false, chờ chấm tay
            is_correct = False

        points = max_points if is_correct else 0
        total_score += points

        detailed.append({
            "questionId": qid,
            "type": q_type,
            "studentAnswer": student_ans_text,
            "isCorrect": is_correct,
            "pointsGained": points,
            "maxPoints": max_points,
            "correctAnswer": correct_ans
        })

    new_result = {
        "id": str(uuid4()),
        "studentId": data.get("studentId"),
        "testId": test_id,
        "assignmentId": data.get("assignmentId"),
        "studentAnswers": student_answers,
        "detailedResults": detailed,
        "totalScore": total_score,
        "submittedAt": datetime.datetime.utcnow().isoformat()
    }

    db.results.insert_one(new_result)
    new_result.pop("_id", None)
    return jsonify(new_result), 201




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
