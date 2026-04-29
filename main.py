import hashlib
import os
from collections import Counter, defaultdict
from datetime import datetime, timezone
from urllib.parse import urlencode

from azure.core.exceptions import ResourceExistsError, ResourceNotFoundError
from azure.storage.blob import BlobServiceClient, ContentSettings
from bson import ObjectId
from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
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


def formatTimestamp(value):
    if value is None:
        return "-"
    return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


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
files.create_index([("shared_with_user_ids", ASCENDING)])


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
        if email and user.get("email") != email:
            users.update_one({"_id": user["_id"]}, {"$set": {"email": email}})
            user["email"] = email
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


def buildBreadcrumbs(current):
    crumbs = []
    node = current
    while node is not None:
        crumbs.append({"id": str(node["_id"]), "name": node["name"]})
        if node.get("parent_directory_id") is None:
            break
        node = directories.find_one({"_id": node["parent_directory_id"]})
    crumbs.reverse()
    if crumbs:
        crumbs[0]["name"] = "/"
    return crumbs


def buildContext(request, user_token=None, user=None, current=None, message=None, error=None):
    ctx = {
        "request": request, "user_token": user_token, "user_info": None,
        "current_directory": None, "directories": [], "files": [],
        "breadcrumbs": [], "parent_directory": None, "duplicate_groups": [],
        "shared_files": [], "existing_file_names": [],
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

    counts = Counter(f.get("sha256") for f in cur_files if f.get("sha256"))
    dup_in_dir = {h for h, c in counts.items() if c > 1}

    parent = None
    if current.get("parent_directory_id") is not None:
        parent = directories.find_one(
            {"_id": current["parent_directory_id"], "owner_user_id": user["user_id"]}
        )

    file_views = []
    for f in cur_files:
        sha = f.get("sha256") or ""
        path = f.get("directory_path") or "/"
        full = f"/{f['name']}" if path == "/" else f"{path}/{f['name']}"
        file_views.append({
            "id": str(f["_id"]), "name": f["name"], "size": formatSize(f.get("size")),
            "content_type": f.get("content_type") or "application/octet-stream",
            "sha256": sha, "sha256_short": sha[:12] if sha else "-",
            "updated_at": formatTimestamp(f.get("updated_at")),
            "full_path": full, "shared_with": f.get("shared_with_emails", []),
            "is_duplicate_current": sha in dup_in_dir,
        })

    all_files = list(files.find({"owner_user_id": user["user_id"]}))
    grouped = defaultdict(list)
    for f in all_files:
        if f.get("sha256"):
            grouped[f["sha256"]].append(f)
    dup_groups = []
    for sha, group in grouped.items():
        if len(group) < 2:
            continue
        dup_groups.append({"sha256": sha, "files": [
            {"id": str(g["_id"]), "name": g["name"],
             "full_path": ((g.get("directory_path") or "/") + "/" + g["name"]).replace("//", "/"),
             "size": formatSize(g.get("size"))}
            for g in sorted(group, key=lambda x: x["name"])
        ]})
    dup_groups.sort(key=lambda g: (-len(g["files"]), g["sha256"]))

    shared_docs = list(files.find({"shared_with_user_ids": user["user_id"]}).sort("name", ASCENDING))
    owner_emails = {}
    if shared_docs:
        owner_ids = {d["owner_user_id"] for d in shared_docs}
        for u in users.find({"user_id": {"$in": list(owner_ids)}}):
            owner_emails[u["user_id"]] = u.get("email") or u["user_id"]
    shared_files = [{
        "id": str(d["_id"]), "name": d["name"],
        "owner": owner_emails.get(d["owner_user_id"], d["owner_user_id"]),
        "full_path": ((d.get("directory_path") or "/") + "/" + d["name"]).replace("//", "/"),
        "size": formatSize(d.get("size")),
    } for d in shared_docs]

    ctx["user_info"] = {"email": user.get("email") or "", "root_directory_id": str(user["root_directory_id"])}
    ctx["current_directory"] = {
        "id": str(current["_id"]), "name": current["name"], "path": current["path"],
        "created_at": formatTimestamp(current.get("created_at")),
    }
    ctx["directories"] = [
        {"id": str(d["_id"]), "name": d["name"], "path": d["path"]} for d in sub_dirs
    ]
    ctx["files"] = file_views
    ctx["breadcrumbs"] = buildBreadcrumbs(current)
    ctx["parent_directory"] = (
        {"id": str(parent["_id"]), "name": parent["name"], "path": parent["path"]}
        if parent else None
    )
    ctx["duplicate_groups"] = dup_groups
    ctx["shared_files"] = shared_files
    ctx["existing_file_names"] = [f["name"] for f in cur_files]
    return ctx


# ============================================================
# routes
# ============================================================

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


@app.post("/delete-directory")
async def deleteDirectory(request: Request,
                          directory_id: str = Form(...),
                          current_directory_id: str = Form(...)):
    token = validateFirebaseToken(request)
    if token is None:
        return goHome(error="Please sign in before deleting a directory.")
    user = getUser(token)
    target = directories.find_one(
        {"_id": parseObjectId(directory_id), "owner_user_id": user["user_id"]}
    )
    if target is None:
        return goHome(current_directory_id, error="The directory was not found.")
    if target["_id"] == user["root_directory_id"]:
        return goHome(current_directory_id, error="The root directory cannot be deleted.")
    has_child_dir = directories.find_one(
        {"owner_user_id": user["user_id"], "parent_directory_id": target["_id"]}
    )
    has_child_file = files.find_one(
        {"owner_user_id": user["user_id"], "directory_id": target["_id"]}
    )
    if has_child_dir or has_child_file:
        return goHome(current_directory_id, error="You can only delete an empty directory.")
    directories.delete_one({"_id": target["_id"], "owner_user_id": user["user_id"]})
    redirect_id = current_directory_id
    if directory_id == current_directory_id:
        redirect_id = str(target.get("parent_directory_id") or user["root_directory_id"])
    return goHome(redirect_id, message=f"Directory '{target['name']}' deleted.")


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
        return goHome(current_directory_id, error="This filename already exists. Confirm overwrite and try again.")

    content = await upload.read()
    sha = hashlib.sha256(content).hexdigest()
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
        "sha256": sha, "updated_at": utcNow(),
    }
    if existing is None:
        files.insert_one({**fields, "created_at": utcNow(),
                          "shared_with_user_ids": [], "shared_with_emails": []})
        return goHome(current_directory_id, message=f"Uploaded '{filename}'.")
    files.update_one({"_id": existing["_id"]}, {"$set": fields})
    return goHome(current_directory_id, message=f"Overwrote '{filename}'.")


@app.post("/delete-file")
async def deleteFile(request: Request,
                     file_id: str = Form(...),
                     current_directory_id: str = Form(...)):
    token = validateFirebaseToken(request)
    if token is None:
        return goHome(error="Please sign in before deleting files.")
    user = getUser(token)
    file_doc = files.find_one(
        {"_id": parseObjectId(file_id), "owner_user_id": user["user_id"]}
    )
    if file_doc is None:
        return goHome(current_directory_id, error="The file was not found.")
    try:
        container.get_blob_client(file_doc["blob_name"]).delete_blob()
    except ResourceNotFoundError:
        pass
    files.delete_one({"_id": file_doc["_id"], "owner_user_id": user["user_id"]})
    return goHome(current_directory_id, message=f"Deleted '{file_doc['name']}'.")


@app.get("/download-file/{file_id}")
async def downloadFile(request: Request, file_id: str):
    token = validateFirebaseToken(request)
    if token is None:
        return goHome(error="Please sign in before downloading files.")
    user = getUser(token)
    file_doc = files.find_one({"_id": parseObjectId(file_id)})
    if file_doc is None:
        return goHome(error="The requested file was not found.")
    is_owner = file_doc.get("owner_user_id") == user["user_id"]
    is_shared = user["user_id"] in file_doc.get("shared_with_user_ids", [])
    if not (is_owner or is_shared):
        return goHome(error="You do not have access to that file.")
    try:
        stream = container.get_blob_client(file_doc["blob_name"]).download_blob()
    except ResourceNotFoundError:
        return goHome(error="The requested blob could not be found.")
    headers = {"Content-Disposition": f'attachment; filename="{file_doc["name"]}"'}
    return StreamingResponse(
        stream.chunks(),
        media_type=file_doc.get("content_type") or "application/octet-stream",
        headers=headers,
    )


@app.post("/share-file")
async def shareFile(request: Request,
                    file_id: str = Form(...),
                    current_directory_id: str = Form(...),
                    share_emails: str = Form(...)):
    token = validateFirebaseToken(request)
    if token is None:
        return goHome(error="Please sign in before sharing files.")
    user = getUser(token)
    file_doc = files.find_one(
        {"_id": parseObjectId(file_id), "owner_user_id": user["user_id"]}
    )
    if file_doc is None:
        return goHome(current_directory_id, error="The file was not found.")
    targets = sorted({e.strip().lower() for e in share_emails.split(",") if e.strip()})
    if not targets:
        return goHome(current_directory_id, error="Enter at least one email address.")

    shared_ids = set(file_doc.get("shared_with_user_ids", []))
    shared_emails_set = set(file_doc.get("shared_with_emails", []))
    missing, added = [], 0
    own_email = (user.get("email") or "").lower()
    for email in targets:
        if email == own_email:
            continue
        target_user = users.find_one({"email": email})
        if target_user is None:
            missing.append(email)
            continue
        if target_user["user_id"] not in shared_ids:
            added += 1
        shared_ids.add(target_user["user_id"])
        shared_emails_set.add(target_user["email"])

    files.update_one({"_id": file_doc["_id"]}, {"$set": {
        "shared_with_user_ids": sorted(shared_ids),
        "shared_with_emails": sorted(shared_emails_set),
        "updated_at": utcNow(),
    }})
    err_msg = "These emails do not have accounts yet: " + ", ".join(missing) if missing else None
    msg = (f"Shared '{file_doc['name']}' with {added} account(s)." if added
           else f"No new shares were added for '{file_doc['name']}'.")
    return goHome(current_directory_id, message=msg, error=err_msg)
