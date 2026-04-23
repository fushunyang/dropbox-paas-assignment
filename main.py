from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from google.auth.transport.requests import Request as FirebaseRequestAdapter
from google.oauth2 import id_token


app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")
firebaseRequestAdapter = FirebaseRequestAdapter()


def validateFirebaseToken(request):
    cookie = request.cookies.get("token")
    if not cookie:
        return None
    try:
        return id_token.verify_firebase_token(cookie, firebaseRequestAdapter)
    except Exception as e:
        print(e)
        return None


@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    token = validateFirebaseToken(request)
    return templates.TemplateResponse(request, "main.html", {
        "request": request, "user_token": token,
    })
