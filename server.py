from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from pymongo import MongoClient
from uuid import uuid4
import os
from dotenv import load_dotenv
from werkzeug.exceptions import HTTPException
from werkzeug.utils import secure_filename # <-- THÊM DÒNG NÀY
import datetime

# Load .env in local; Render provides env vars automatically
load_dotenv()

app = Flask(__name__, static_folder="static", template_folder="templates")
# Allow all origins so frontend on any domain can call this API
CORS(app, resources={r"/*": {"origins": "*"}})

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

# --------------------- CẤU HÌNH UPLOAD FILE ---------------------
UPLOAD_FOLDER = 'uploads'
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
# Đảm bảo thư mục 'uploads' tồn tại
os.makedirs(UPLOAD_FOLDER, exist_ok=True) 

# Hàm kiểm tra định dạng file
def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# --------------------- HÀM TIỆN ÍCH ---------------------
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
        # Trả về lỗi HTTP tiêu chuẩn (ví dụ: 404, 500)
        return jsonify(remove_id(e.get_response().json)), e.code
    
    # Đối với các lỗi khác (lỗi server 500)
    print(f"Lỗi Server: {e}")
    return jsonify({"message": "Đã xảy ra lỗi không xác định trên server."}), 500


# --------------------- FILE UPLOADS API ---------------------
@app.route("/api/upload-image", methods=["POST"])
def upload_file():
    # 1. Kiểm tra xem file có trong request không
    if 'image' not in request.files: 
        return jsonify({"message": "Không tìm thấy file 'image' trong request."}), 400
    
    file = request.files['image']
    
    # 2. Kiểm tra file có được chọn không
    if file.filename == '':
        return jsonify({"message": "Không có file được chọn."}), 400
    
    # 3. Kiểm tra định dạng file
    if file and allowed_file(file.filename):
        # Tạo tên file bảo mật (sanitized)
        filename = secure_filename(file.filename)
        
        # Tạo tên file độc nhất bằng UUID để tránh bị ghi đè
        file_extension = filename.rsplit('.', 1)[1].lower()
        unique_filename = f"{uuid4().hex}.{file_extension}" 
        
        # Lưu file vào thư mục UPLOAD_FOLDER
        save_path = os.path.join(app.config['UPLOAD_FOLDER'], unique_filename)
        file.save(save_path)
        
        # Trả về URL công khai của file đã lưu
        # Dùng request.host_url để lấy base URL (ví dụ: http://localhost:3000/)
        file_url = f"{request.host_url}{app.config['UPLOAD_FOLDER']}/{unique_filename}"
        
        return jsonify({"message": "Tải ảnh lên thành công.", "url": file_url}), 201
    else:
        return jsonify({"message": "Định dạng file không được phép. Chỉ chấp nhận: png, jpg, jpeg, gif."}), 400


# --------------------- QUESTIONS API ---------------------
@app.route("/questions", methods=["GET"])
@app.route("/api/questions", methods=["GET"])
def list_questions():
    query = {}
    subject = request.args.get("subject")
    level = request.args.get("level")
    testId = request.args.get("testId")
    if subject: query["subject"] = subject
    if level: query["level"] = level
    if testId: query["testId"] = testId
    docs = list(db.questions.find(query, {"_id": 0}))
    return jsonify(docs)

@app.route("/questions", methods=["POST"])
@app.route("/api/questions", methods=["POST"])
def create_question():
    data = request.get_json() or {}
    # newq sẽ bao gồm cả trường 'imageUrl' nếu frontend gửi lên
    newq = {"id": str(uuid4()), **data} 
    db.questions.insert_one(newq)
    to_return = newq.copy(); to_return.pop("_id", None)
    return jsonify(to_return), 201

@app.route("/questions/<question_id>", methods=["PUT"])
@app.route("/api/questions/<question_id>", methods=["PUT"])
def update_question(question_id):
    data = request.get_json() or {}
    # Bỏ trường id ra khỏi dữ liệu cập nhật để tránh ghi đè
    data.pop("id", None) 
    
    updated_q = db.questions.find_one_and_update(
        {"id": question_id},
        {"$set": data},
        return_document=True
    )
    if not updated_q: return jsonify({"message": "Câu hỏi không tìm thấy."}), 404
    return jsonify(remove_id(updated_q))

@app.route("/questions/<question_id>", methods=["DELETE"])
@app.route("/api/questions/<question_id>", methods=["DELETE"])
def delete_question(question_id):
    result = db.questions.delete_one({"id": question_id})
    if result.deleted_count == 0:
        return jsonify({"message": "Câu hỏi không tìm thấy."}), 404
    return jsonify({"message": "Xóa câu hỏi thành công."}), 200

# --------------------- TESTS API ---------------------
@app.route("/tests", methods=["GET"])
@app.route("/api/tests", methods=["GET"])
def list_tests():
    docs = list(db.tests.find({}, {"_id": 0}))
    return jsonify(docs)

@app.route("/tests", methods=["POST"])
@app.route("/api/tests", methods=["POST"])
def create_test():
    data = request.get_json() or {}
    newt = {"id": str(uuid4()), **data, "createdAt": datetime.datetime.utcnow().isoformat()}
    db.tests.insert_one(newt)
    to_return = newt.copy(); to_return.pop("_id", None)
    return jsonify(to_return), 201

@app.route("/tests/<test_id>", methods=["GET"])
@app.route("/api/tests/<test_id>", methods=["GET"])
def get_test(test_id):
    doc = db.tests.find_one({"id": test_id}, {"_id": 0})
    if not doc: return jsonify({"message": "Đề thi không tìm thấy."}), 404
    return jsonify(doc)

@app.route("/tests/<test_id>", methods=["DELETE"])
@app.route("/api/tests/<test_id>", methods=["DELETE"])
def delete_test(test_id):
    result = db.tests.delete_one({"id": test_id})
    # Đồng thời xóa các câu hỏi thuộc về đề thi này
    db.questions.delete_many({"testId": test_id})
    if result.deleted_count == 0:
        return jsonify({"message": "Đề thi không tìm thấy."}), 404
    return jsonify({"message": "Xóa đề thi thành công."}), 200


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


# --------------------- SERVE UPLOADED FILES (QUAN TRỌNG) ---------------------
@app.route(f"/{UPLOAD_FOLDER}/<filename>")
def uploaded_file(filename):
    # Cho phép server trả về file từ thư mục 'uploads'
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)


# Serve frontend files (unchanged)
@app.route("/", methods=["GET"])
def index():
    try:
        return send_from_directory(".", "index.html")
        return app.send_static_file("index.html")
    except:
        return "Frontend not built or index.html not found.", 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT, debug=True)
