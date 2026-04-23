import { initializeApp } from "https://www.gstatic.com/firebasejs/10.12.5/firebase-app.js";
import {
  createUserWithEmailAndPassword,
  getAuth,
  signInWithEmailAndPassword,
  signOut,
} from "https://www.gstatic.com/firebasejs/10.12.5/firebase-auth.js";

const firebaseConfig = {
  apiKey: "AIzaSyCVTVgml1dbl1uN38fM4hzMOvQagOCJ7Co",
  authDomain: "project-4935159403951686756.firebaseapp.com",
  projectId: "project-4935159403951686756",
  storageBucket: "project-4935159403951686756.firebasestorage.app",
  messagingSenderId: "321504476967",
  appId: "1:321504476967:web:6b4ed0369d0278226cb302",
};

const app = initializeApp(firebaseConfig);
const auth = getAuth(app);

const signupButton = document.getElementById("signup");
const loginButton = document.getElementById("login");
const signOutButton = document.getElementById("sign-out");

updateUI(document.cookie);

if (signupButton) {
  signupButton.addEventListener("click", async () => {
    const email = document.getElementById("email").value;
    const password = document.getElementById("password").value;

    try {
      const userCredential = await createUserWithEmailAndPassword(auth, email, password);
      const token = await userCredential.user.getIdToken();
      document.cookie = `token=${token}; path=/; SameSite=Strict`;
      window.location = "/";
    } catch (error) {
      console.error(error);
    }
  });
}

if (loginButton) {
  loginButton.addEventListener("click", async () => {
    const email = document.getElementById("email").value;
    const password = document.getElementById("password").value;

    try {
      const userCredential = await signInWithEmailAndPassword(auth, email, password);
      const token = await userCredential.user.getIdToken();
      document.cookie = `token=${token}; path=/; SameSite=Strict`;
      window.location = "/";
    } catch (error) {
      console.error(error);
    }
  });
}

if (signOutButton) {
  signOutButton.addEventListener("click", async () => {
    try {
      await signOut(auth);
    } catch (error) {
      console.error(error);
    }
    document.cookie = "token=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/; SameSite=Strict";
    window.location = "/";
  });
}

function updateUI(cookieString) {
  const loginBox = document.getElementById("login-box");
  const tokenValue = parseCookieToken(cookieString);
  if (!loginBox || !signOutButton) {
    return;
  }

  if (tokenValue.length > 0) {
    loginBox.hidden = true;
    signOutButton.hidden = false;
    return;
  }

  loginBox.hidden = false;
  signOutButton.hidden = true;
}

function parseCookieToken(cookieString) {
  const cookieParts = cookieString.split(";");
  for (const cookiePart of cookieParts) {
    const parts = cookiePart.split("=", 2);
    if (parts.length !== 2) {
      continue;
    }

    const key = parts[0].trim();
    const value = parts[1].trim();
    if (key === "token") {
      return value;
    }
  }

  return "";
}
