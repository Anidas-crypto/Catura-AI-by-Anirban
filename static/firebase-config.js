// ============================
// 🔥 FIREBASE CONFIGURATION
// ============================
const firebaseConfig = {
    apiKey: "YOUR_FIREBASE_API_KEY",
    authDomain: "YOUR_FIREBASE_PROJECT.firebaseapp.com",
    databaseURL: "https://YOUR_FIREBASE_PROJECT.firebaseio.com",
    projectId: "YOUR_FIREBASE_PROJECT",
    storageBucket: "YOUR_FIREBASE_PROJECT.appspot.com",
    messagingSenderId: "YOUR_MESSAGING_SENDER_ID",
    appId: "YOUR_APP_ID"
};

// Initialize Firebase
const firebaseApp = firebase.initializeApp(firebaseConfig);
const feedbackDatabase = firebase.database();

console.log("✅ Firebase initialized for feedback");