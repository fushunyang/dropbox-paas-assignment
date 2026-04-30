# Assignment2

This is my Cloud Services & Platforms assignment. It is a small
Dropbox-style file manager: Firebase is used for login, MongoDB Atlas
keeps the user/directory/file records, and Azurite stores the uploaded
file data locally. I kept the project close to the course examples
(Example 03 / 04 / 10), including the required `firebase-login.js` file.
Only the `firebaseConfig` block in that script should need changing.

## Files

```
main.py                   all routes and helpers in one file
templates/main.html       the only page
static/styles.css         styling
static/firebase-login.js  Firebase login script for the browser
requirements.txt          Python dependencies
docs/documentation.pdf    written documentation
```

## Running it

```powershell
python -m venv env
.\env\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn main:app --reload
```

Then open `http://127.0.0.1:8000`. The MongoDB Atlas URI and the Azurite
connection string are already in `main.py`, so local testing does not
need extra environment variables. Azurite still has to be running before
file upload/download will work.

## Firebase setup

Email/Password sign-in must be enabled in the Firebase Authentication
console. In `static/firebase-login.js`, point `firebaseConfig` at the
Firebase project being used for testing. The rest of that file is left in
the same style as the course examples.

## Implemented

- Firebase email/password login and logout via the `token` cookie
- `users`, `directories`, `files` collections in MongoDB with unique
  indexes that prevent duplicate names in the same place
- Root directory `/` is created automatically on first login
- Create a directory / delete an empty directory
- Open a directory and go up with `../` (hidden at root)
- Upload a file, with confirm-before-overwrite when the name already exists
- Download and delete files
- Non-empty directories cannot be deleted
- SHA-256 hashing on upload to flag duplicates in the current directory
- Account-wide duplicate list showing matching paths together
- Read-only sharing of files with other accounts that already exist
