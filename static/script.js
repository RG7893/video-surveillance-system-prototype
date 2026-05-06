document.addEventListener('DOMContentLoaded', function() {
    const signUpButton = document.getElementById('signUpButton');
    const signInButton = document.getElementById('signInButton');
    
    if (signUpButton) {
        signUpButton.addEventListener('click', function(e) {
            e.preventDefault();
            document.getElementById('signIn').style.display = "none";
            document.getElementById('signup').style.display = "block";
        });
    }
    
    if (signInButton) {
        signInButton.addEventListener('click', function(e) {
            e.preventDefault();
            document.getElementById('signup').style.display = "none";
            document.getElementById('signIn').style.display = "block";
        });
    }
});