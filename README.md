# Dropbox PaaS Assignment

Cloud Services & Platforms - assignment 2.

## How to run

```powershell
python -m venv env
.\env\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn main:app --reload
```

Then open `http://127.0.0.1:8000`.

## Implemented so far

- Firebase email/password login + logout
- MongoDB users / directories / files collections with unique indexes
- Create / delete directory
- Upload / download / delete files (Azurite blob)
- Confirm-before-overwrite on upload
- SHA-256 duplicate detection in current directory and across the whole account
- Read-only sharing with other accounts
