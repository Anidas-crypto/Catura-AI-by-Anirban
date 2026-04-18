// ============================
// 🔥 FIREBASE CONFIGURATION
// ============================
const firebaseConfig = {
    apiKey: "AIzaSyDT_FwVCzkVXUFwX6FRxRQNv0FKlqtPsrU",
    authDomain: "catura-ai-feedback-req-form.firebaseapp.com",
    databaseURL: "https://catura-ai-feedback-req-form-default-rtdb.asia-southeast1.firebasedatabase.app",
    projectId: "catura-ai-feedback-req-form",
    storageBucket: "catura-ai-feedback-req-form.firebasestorage.app",
    messagingSenderId: "126962844999",
    appId: "1:126962844999:web:6d896bac7a65e6f160f862"
};

// Initialize Firebase
const firebaseApp = firebase.initializeApp(firebaseConfig);
const feedbackDatabase = firebase.database();

console.log("✅ Firebase initialized for feedback");