import os
from datetime import datetime, timezone

from bson import ObjectId
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from google.auth.transport.requests import Request as FirebaseRequestAdapter
from google.oauth2 import id_token
from pymongo import ASCENDING, MongoClient
from pymongo.errors import DuplicateKeyError


# ---------- config ----------
DEFAULT_MONGODB_URI = (
    "mongodb+srv://924759663:T12345678@cluster0.jiikkqk.mongodb.net/"
    "?retryWrites=true&w=majority&appName=Cluster0"
)
MONGODB_URI = os.environ.get("MONGODB_URI") or DEFAULT_MONGODB_URI
DATABASE_NAME = os.environ.get("MONGODB_DATABASE", "dropbox_assignment")


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

    now = datetime.now(timezone.utc)
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
