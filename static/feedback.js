name=static/feedback.js
// ============================
// 💬 FEEDBACK SYSTEM
// ============================

let feedbackType = 'bug'; // 'bug' or 'feature'

// ============================
// 🎯 OPEN FEEDBACK MODAL
// ============================
window.openFeedbackModal = function(type) {
    feedbackType = type;
    
    const modal = document.getElementById('feedbackModal');
    const title = document.getElementById('feedbackModalTitle');
    const form = document.getElementById('feedbackForm');
    const message = document.getElementById('feedbackMessage');
    
    // Reset form and message
    form.style.display = 'block';
    message.style.display = 'none';
    form.reset();
    
    // Set title based on type
    if (type === 'bug') {
        title.textContent = '🐛 Send Bug Report';
    } else {
        title.textContent = '⭐ Request a Feature';
    }
    
    // Pre-fill email if user is logged in
    const emailInput = document.getElementById('feedbackEmail');
    const nameInput = document.getElementById('feedbackName');
    
    if (currentUser && currentUser.email) {
        emailInput.value = currentUser.email;
    }
    
    if (currentUser) {
        const fullName = document.getElementById("userFullname")?.textContent || "User";
        nameInput.value = fullName;
    }
    
    // Show modal
    modal.classList.add('active');
};

// ============================
// ❌ CLOSE FEEDBACK MODAL
// ============================
window.closeFeedbackModal = function() {
    const modal = document.getElementById('feedbackModal');
    modal.classList.remove('active');
    document.getElementById('feedbackForm').reset();
};

// ============================
// 📤 SUBMIT FEEDBACK
// ============================
document.addEventListener('DOMContentLoaded', function() {
    const feedbackForm = document.getElementById('feedbackForm');
    
    if (feedbackForm) {
        feedbackForm.addEventListener('submit', async function(e) {
            e.preventDefault();
            
            const submitBtn = document.getElementById('feedbackSubmitBtn');
            const originalText = submitBtn.innerHTML;
            
            // Disable button and show loading state
            submitBtn.disabled = true;
            submitBtn.innerHTML = '<span>Submitting...</span>';
            
            try {
                // Get form values
                const name = document.getElementById('feedbackName').value.trim();
                const email = document.getElementById('feedbackEmail').value.trim();
                const message = document.getElementById('feedbackMessage').value.trim();
                
                // Validate
                if (!name || !email || !message) {
                    showFeedbackMessage('❌ Please fill in all fields', 'error');
                    submitBtn.disabled = false;
                    submitBtn.innerHTML = originalText;
                    return;
                }
                
                // Create feedback object
                const feedbackData = {
                    type: feedbackType,
                    name: name,
                    email: email,
                    message: message,
                    userId: currentUser?.id || 'anonymous',
                    userEmail: currentUser?.email || 'not-logged-in',
                    timestamp: new Date().toISOString(),
                    date: new Date().toLocaleDateString(),
                    time: new Date().toLocaleTimeString()
                };
                
                // Save to Firebase Realtime Database
                const feedbackRef = feedbackDatabase.ref('feedback').push();
                
                feedbackRef.set(feedbackData)
                    .then(() => {
                        console.log('✅ Feedback submitted successfully');
                        showFeedbackMessage(
                            `✅ ${feedbackType === 'bug' ? 'Bug report' : 'Feature request'} submitted successfully! Thank you for your feedback.`,
                            'success'
                        );
                        
                        // Reset form
                        feedbackForm.reset();
                        
                        // Close modal after 2 seconds
                        setTimeout(() => {
                            closeFeedbackModal();
                        }, 2000);
                    })
                    .catch((error) => {
                        console.error('❌ Firebase error:', error);
                        showFeedbackMessage(
                            `❌ Failed to submit feedback. Please try again. Error: ${error.message}`,
                            'error'
                        );
                    })
                    .finally(() => {
                        submitBtn.disabled = false;
                        submitBtn.innerHTML = originalText;
                    });
                    
            } catch (error) {
                console.error('❌ Error:', error);
                showFeedbackMessage('❌ An unexpected error occurred. Please try again.', 'error');
                submitBtn.disabled = false;
                submitBtn.innerHTML = originalText;
            }
        });
    }
});

// ============================
// 💬 SHOW FEEDBACK MESSAGE
// ============================
function showFeedbackMessage(text, type) {
    const messageEl = document.getElementById('feedbackMessage');
    const form = document.getElementById('feedbackForm');
    
    messageEl.textContent = text;
    messageEl.className = `feedback-message feedback-message-${type}`;
    messageEl.style.display = 'block';
    
    if (type === 'success') {
        form.style.display = 'none';
    }
}

// Close modal when clicking outside
document.addEventListener('DOMContentLoaded', function() {
    const modalOverlay = document.querySelector('.feedback-modal-overlay');
    if (modalOverlay) {
        modalOverlay.addEventListener('click', closeFeedbackModal);
    }
});