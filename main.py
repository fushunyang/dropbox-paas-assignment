import os
from datetime import datetime, timezone
from urllib.parse import urlencode

from azure.storage.blob import BlobServiceClient
from bson import ObjectId
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from google.auth.transport.requests import Request as FirebaseRequestAdapter
from google.oauth2 import id_token
from pymongo import ASCENDING, MongoClient
from pymongo.errors import DuplicateKeyError
from starlette import status


# ---------- small helpers ----------
def utcNow():
    return datetime.now(timezone.utc)


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


@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    token = validateFirebaseToken(request)
    user = getUser(token) if token else None
    return templates.TemplateResponse(request, "main.html", {
        "request": request, "user_token": token, "user": user,
    })
