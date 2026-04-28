import os
from datetime import datetime, timezone
from urllib.parse import urlencode

from azure.core.exceptions import ResourceExistsError
from azure.storage.blob import BlobServiceClient, ContentSettings
from bson import ObjectId
from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from google.auth.transport.requests import Request as FirebaseRequestAdapter
from google.oauth2 import id_token
from pymongo import ASCENDING, MongoClient
from pymongo.errors import DuplicateKeyError
from starlette import status


# ---------- config ----------
DEFAULT_MONGODB_URI = (
    "mongodb+srv://924759663:T12345678@cluster0.jiikkqk.mongodb.net/"
    "?retryWrites=true&w=majority&appName=Cluster0"
)
MONGODB_URI = os.environ.get("MONGODB_URI") or DEFAULT_MONGODB_URI
DATABASE_NAME = os.environ.get("MONGODB_DATABASE", "dropbox_assignment")
DEFAULT_AZURITE = (
    "DefaultEndpointsProtocol=http;"
    "AccountName=devstoreaccount1;"
    "AccountKey=Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/K1SZFPTOtr/KBHBeksoGMGw==;"
    "BlobEndpoint=http://127.0.0.1:10000/devstoreaccount1;"
)
AZURE_CONN = os.environ.get("AZURE_STORAGE_CONNECTION_STRING", DEFAULT_AZURITE)
CONTAINER_NAME = os.environ.get("AZURE_STORAGE_CONTAINER", "dropbox-files")


# ---------- FastAPI ----------
app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")
firebaseRequestAdapter = FirebaseRequestAdapter()


# ---------- small helpers ----------
def utcNow():
    return datetime.now(timezone.utc)


def formatSize(size):
    if size is None:
        return "-"
    value = float(size)
    for unit in ["B", "KB", "MB", "GB"]:
        if value < 1024 or unit == "GB":
            return f"{int(value)} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"


def parseObjectId(value):
    if not value:
        return None
    try:
        return ObjectId(value)
    except Exception:
        return None


def goHome(directory_id=None, message=None, error=None):
    params = {}
    if directory_id:
        params["directory_id"] = directory_id
    if message:
        params["message"] = message
    if error:
        params["error"] = error
    url = "/?" + urlencode(params) if params else "/"
    return RedirectResponse(url=url, status_code=status.HTTP_302_FOUND)


# ---------- MongoDB ----------
mongo_client = MongoClient(MONGODB_URI, serverSelectionTimeoutMS=5000)
db = mongo_client[DATABASE_NAME]
users = db["users"]
directories = db["directories"]
files = db["files"]
users.create_index([("user_id", ASCENDING)], unique=True)
users.create_index([("email", ASCENDING)], unique=True, sparse=True)
directories.create_index(
    [("owner_user_id", ASCENDING), ("parent_directory_id", ASCENDING), ("name", ASCENDING)],
    unique=True,
)
files.create_index(
    [("owner_user_id", ASCENDING), ("directory_id", ASCENDING), ("name", ASCENDING)],
    unique=True,
)


# ---------- Azurite blob ----------
blob_service = BlobServiceClient.from_connection_string(
    AZURE_CONN, connection_timeout=2, read_timeout=2, retry_total=1,
)
container = blob_service.get_container_client(CONTAINER_NAME)


def validateFirebaseToken(request):
    cookie = request.cookies.get("token")
    if not cookie:
        return None
    try:
        return id_token.verify_firebase_token(cookie, firebaseRequestAdapter)
    except Exception as e:
        print(e)
        return None


def getUser(user_token):
    fb_uid = user_token.get("user_id") or user_token.get("uid") or user_token.get("sub") or ""
    email = (user_token.get("email") or "").strip().lower()

    user = users.find_one({"user_id": fb_uid})
    if user is not None:
        return user

    now = utcNow()
    root_id = ObjectId()
    try:
        users.insert_one({
            "user_id": fb_uid, "email": email,
            "root_directory_id": root_id, "created_at": now,
        })
        directories.insert_one({
            "_id": root_id, "owner_user_id": fb_uid, "name": "/",
            "parent_directory_id": None, "path": "/", "created_at": now,
        })
    except DuplicateKeyError:
        pass
    return users.find_one({"user_id": fb_uid})


def getCurrentDirectory(user, directory_id_text):
    root = directories.find_one({"_id": user["root_directory_id"], "owner_user_id": user["user_id"]})
    if root is None:
        return None
    if not directory_id_text:
        return root
    target = directories.find_one(
        {"_id": parseObjectId(directory_id_text), "owner_user_id": user["user_id"]}
    )
    return target or root


def buildContext(request, user_token=None, user=None, current=None, message=None, error=None):
    ctx = {
        "request": request, "user_token": user_token,
        "current_directory": None, "directories": [], "files": [],
        "existing_file_names": [],
        "message": message, "error": error,
    }
    if user is None or current is None:
        return ctx
    sub_dirs = list(directories.find(
        {"owner_user_id": user["user_id"], "parent_directory_id": current["_id"]}
    ).sort("name", ASCENDING))
    cur_files = list(files.find(
        {"owner_user_id": user["user_id"], "directory_id": current["_id"]}
    ).sort("name", ASCENDING))
    ctx["current_directory"] = {
        "id": str(current["_id"]), "name": current["name"], "path": current["path"],
    }
    ctx["directories"] = [
        {"id": str(d["_id"]), "name": d["name"], "path": d["path"]} for d in sub_dirs
    ]
    ctx["files"] = [{
        "id": str(f["_id"]), "name": f["name"], "size": formatSize(f.get("size")),
    } for f in cur_files]
    ctx["existing_file_names"] = [f["name"] for f in cur_files]
    return ctx


@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    msg = request.query_params.get("message")
    err = request.query_params.get("error")
    token = validateFirebaseToken(request)
    if token is None:
        return templates.TemplateResponse(request, "main.html", buildContext(request, message=msg, error=err))
    user = getUser(token)
    current = getCurrentDirectory(user, request.query_params.get("directory_id"))
    return templates.TemplateResponse(request, "main.html", buildContext(
        request, user_token=token, user=user, current=current, message=msg, error=err,
    ))


@app.post("/create-directory")
async def createDirectory(request: Request,
                          current_directory_id: str = Form(...),
                          directory_name: str = Form(...)):
    token = validateFirebaseToken(request)
    if token is None:
        return goHome(error="Please sign in before creating a directory.")
    user = getUser(token)
    current = directories.find_one(
        {"_id": parseObjectId(current_directory_id), "owner_user_id": user["user_id"]}
    )
    if current is None:
        return goHome(error="The target directory could not be found.")
    name = (directory_name or "").strip()
    if not name or name in [".", "..", "/"] or "/" in name or "\\" in name:
        return goHome(current_directory_id, error="Please choose a normal directory name.")
    new_path = f"/{name}" if current["path"] == "/" else f"{current['path']}/{name}"
    try:
        directories.insert_one({
            "owner_user_id": user["user_id"], "name": name,
            "parent_directory_id": current["_id"], "path": new_path,
            "created_at": utcNow(),
        })
    except DuplicateKeyError:
        return goHome(current_directory_id, error="A directory with that name already exists here.")
    return goHome(current_directory_id, message=f"Directory '{name}' created.")


@app.post("/upload-file")
async def uploadFile(request: Request,
                     current_directory_id: str = Form(...),
                     overwrite: str = Form("0"),
                     upload: UploadFile = File(...)):
    token = validateFirebaseToken(request)
    if token is None:
        return goHome(error="Please sign in before uploading files.")
    user = getUser(token)
    current = directories.find_one(
        {"_id": parseObjectId(current_directory_id), "owner_user_id": user["user_id"]}
    )
    if current is None:
        return goHome(error="The target directory could not be found.")
    filename = (upload.filename or "").strip()
    if not filename:
        return goHome(current_directory_id, error="Please choose a file first.")
    existing = files.find_one(
        {"owner_user_id": user["user_id"], "directory_id": current["_id"], "name": filename}
    )
    if existing is not None and overwrite != "1":
        return goHome(current_directory_id, error="That filename already exists. Confirm overwrite.")

    content = await upload.read()
    blob_name = f"{user['user_id']}/{current['_id']}/{filename}"
    try:
        try:
            container.create_container()
        except ResourceExistsError:
            pass
        container.get_blob_client(blob_name).upload_blob(
            content, overwrite=True,
            content_settings=ContentSettings(content_type=upload.content_type or "application/octet-stream"),
        )
    except Exception:
        return goHome(current_directory_id, error="Blob storage upload failed. Check Azurite.")

    fields = {
        "name": filename, "owner_user_id": user["user_id"],
        "directory_id": current["_id"], "directory_path": current["path"],
        "blob_name": blob_name, "size": len(content),
        "content_type": upload.content_type or "application/octet-stream",
        "updated_at": utcNow(),
    }
    if existing is None:
        files.insert_one({**fields, "created_at": utcNow()})
        return goHome(current_directory_id, message=f"Uploaded '{filename}'.")
    files.update_one({"_id": existing["_id"]}, {"$set": fields})
    return goHome(current_directory_id, message=f"Overwrote '{filename}'.")
